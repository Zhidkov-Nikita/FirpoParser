"""
Playwright-based scraper for FIRPO Edu platform (Quasar SPA).

Target: https://edu.firpo.ru/jM5a-1Pq8/students
Extracts student personal info and enrollment route data from dialog windows.

Uses synchronous Playwright API (sync_api) + direct Django ORM calls.
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from playwright.sync_api import (
    sync_playwright,
    Page,
    Browser,
    BrowserContext,
    TimeoutError as PwTimeoutError,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StudentData:
    """Raw data extracted from a single student dialog."""
    student_id: Optional[str] = None
    email: Optional[str] = None
    course: Optional[str] = None
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    patronymic: Optional[str] = None

    has_passport_scan: bool = False
    has_name_change_scan: bool = False
    has_education_scan: bool = False

    passport_file_name: Optional[str] = None
    passport_file_bytes: Optional[bytes] = None

    name_change_file_name: Optional[str] = None
    name_change_file_bytes: Optional[bytes] = None

    education_file_name: Optional[str] = None
    education_file_bytes: Optional[bytes] = None

    has_enrollment_application: bool = False
    has_personal_data_consent: bool = False

    route_status: str = "new"
    uploaded_at: Optional[datetime] = None
    operator: Optional[str] = None
    verified_at: Optional[datetime] = None

    raw_fields: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str = "scraper_config.yaml") -> Dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Django ORM save (direct sync call — no sync_to_async needed)
# ---------------------------------------------------------------------------

def save_student_to_db(data: StudentData):
    """
    Saves Student + EnrollmentRoute to Django DB.
    Handles FileField for document scans via ContentFile.
    """
    from django.core.files.base import ContentFile
    from django.db import transaction
    from django.utils.timezone import make_aware, now
    from parser.models import Student, EnrollmentRoute

    if not data.first_name or not data.last_name:
        raise ValueError(
            f"Cannot save without name: last_name={data.last_name!r}, first_name={data.first_name!r}"
        )

    def _ensure_aware(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            return make_aware(dt)
        return dt

    with transaction.atomic():
        student, student_created = Student.objects.update_or_create(
            last_name=data.last_name,
            first_name=data.first_name,
            defaults={
                "patronymic": data.patronymic,
            },
        )

        if data.passport_file_bytes and data.passport_file_name:
            student.passport_file.save(
                data.passport_file_name,
                ContentFile(data.passport_file_bytes),
                save=True,
            )

        if data.name_change_file_bytes and data.name_change_file_name:
            student.name_change_file.save(
                data.name_change_file_name,
                ContentFile(data.name_change_file_bytes),
                save=True,
            )

        if data.education_file_bytes and data.education_file_name:
            student.education_file.save(
                data.education_file_name,
                ContentFile(data.education_file_bytes),
                save=True,
            )

        uploaded_at = _ensure_aware(data.uploaded_at) or now()
        verified_at = _ensure_aware(data.verified_at)

        route, route_created = EnrollmentRoute.objects.update_or_create(
            student=student,
            defaults={
                "status": data.route_status,
                "uploaded_at": uploaded_at,
                "has_enrollment_application": data.has_enrollment_application,
                "has_personal_data_consent": data.has_personal_data_consent,
                "operator": data.operator,
                "verified_at": verified_at,
            },
        )

    return student, route, student_created


# ---------------------------------------------------------------------------
# Browser setup (sync)
# ---------------------------------------------------------------------------

def _build_browser(config: Dict[str, Any]):
    browser_cfg = config.get("browser", {})
    headless = bool(browser_cfg.get("headless", True))

    pw = sync_playwright().start()

    launch_args = {
        "headless": headless,
        "args": [
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-setuid-sandbox",
            "--disable-software-rasterizer",
        ],
    }

    binary = browser_cfg.get("binary_path") or "/usr/bin/chromium-browser"
    launch_args["executable_path"] = binary

    browser = pw.chromium.launch(**launch_args)

    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=browser_cfg.get("user_agent"),
    )
    return pw, browser, context


# ---------------------------------------------------------------------------
# Login (sync)
# ---------------------------------------------------------------------------

def login(page: Page, config: Dict[str, Any]):
    auth = config.get("auth", {})
    login_url = auth.get("login_url")
    if not login_url:
        raise ValueError("auth.login_url not set in config")

    email = auth.get("email") or auth.get("username")
    password = auth.get("password")
    if not email or not password:
        raise ValueError("auth.email and auth.password must be set in config")

    timeout_ms = int(auth.get("login_timeout", 30000))

    email_sel = auth.get("email_selector") or auth.get("username_selector")
    if not email_sel:
        email_sel = "input[aria-label='Электронная почта']"

    password_sel = auth.get("password_selector")
    if not password_sel:
        password_sel = "input[aria-label='Пароль']"

    submit_sel = auth.get("submit_selector")
    if not submit_sel:
        submit_sel = "button.login-button"

    success_url = auth.get("success_url", "")
    success_selector = auth.get("success_selector", ".q-page")

    print(f"[scraper] Navigating to login page: {login_url}")
    page.goto(login_url, wait_until="domcontentloaded", timeout=timeout_ms)

    try:
        page.wait_for_selector(email_sel, timeout=timeout_ms)
    except PwTimeoutError:
        raise RuntimeError(
            f"Login page did not load: email field '{email_sel}' not found."
        )

    print("[scraper] Filling credentials...")
    page.fill(email_sel, email, timeout=timeout_ms)
    page.fill(password_sel, password, timeout=timeout_ms)

    with page.expect_navigation(timeout=timeout_ms) as nav_info:
        page.click(submit_sel, timeout=timeout_ms)

    try:
        response = nav_info.value
        if response and response.status >= 400:
            raise RuntimeError(
                f"Login request returned HTTP {response.status}. "
                "Check your credentials."
            )
    except Exception:
        pass

    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)

    if success_url:
        try:
            page.wait_for_url(f"**{success_url}**", timeout=timeout_ms)
            print(f"[scraper] Navigated to {success_url} — login successful.")
            return
        except PwTimeoutError:
            pass

    if success_selector:
        try:
            page.wait_for_selector(success_selector, timeout=timeout_ms)
            print("[scraper] Success element found — login successful.")
            return
        except PwTimeoutError:
            pass

    current_url = page.url
    if "login" in current_url.lower() or "admin-login" in current_url.lower():
        error_el = page.query_selector(
            ".q-notification, .q-banner, .text-negative, [role='alert']"
        )
        error_text = error_el.inner_text().strip() if error_el else ""
        raise RuntimeError(
            f"Login failed: still on login page after submit. "
            f"URL: {current_url}. "
            f"Error message: {error_text or 'none detected'}. "
            "Check your email and password in scraper_config.yaml."
        )

    print(f"[scraper] Login successful (current URL: {current_url}).")


# ---------------------------------------------------------------------------
# LocalStorage preset injection (sync)
# ---------------------------------------------------------------------------

def inject_localstorage_preset(page: Page, config: Dict[str, Any]):
    pages_cfg = config.get("pages", {})
    preset_key = pages_cfg.get(
        "localstorage_preset_key",
        "preset-students30-students30-all-last",
    )
    preset_value = pages_cfg.get("localstorage_preset_value", "1184")

    print(f"[scraper] Injecting localStorage: {preset_key} = {preset_value}")
    page.evaluate(f"() => localStorage.setItem('{preset_key}', '{preset_value}')")

    actual = page.evaluate(f"() => localStorage.getItem('{preset_key}')")
    if actual == preset_value:
        print(f"[scraper] localStorage verified: {actual}")
    else:
        print(f"[scraper] WARNING: localStorage value mismatch: {actual}")


# ---------------------------------------------------------------------------
# Email collection (sync)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def collect_unique_emails(page: Page, config: Dict[str, Any]) -> List[str]:
    table_cfg = config.get("table", {})
    scroll_container_sel = table_cfg.get(
        "scroll_container_selector",
        "tbody.q-virtual-scroll__content",
    )
    row_selector = table_cfg.get(
        "row_selector",
        "tbody.q-virtual-scroll__content tr",
    )
    max_iterations = int(table_cfg.get("max_scroll_iterations", 50))
    scroll_delay = float(table_cfg.get("scroll_delay", 0.5))

    emails: list[str] = []
    seen: set[str] = set()

    print("[scraper] Collecting unique student emails from table...")

    prev_count = 0
    for i in range(max_iterations):
        rows = page.query_selector_all(row_selector)
        for row in rows:
            row_text = row.inner_text()
            match = _EMAIL_RE.search(row_text)
            if match:
                email = match.group(0)
                if email not in seen:
                    seen.add(email)
                    emails.append(email)

        try:
            page.evaluate("""(sel) => {
                const el = document.querySelector(sel);
                if (el) {
                    const parent = el.closest('.q-table__container') || el.parentElement;
                    if (parent) parent.scrollTop = parent.scrollHeight;
                }
            }""", scroll_container_sel)
            time.sleep(scroll_delay)
        except Exception:
            break

        new_rows = page.query_selector_all(row_selector)
        new_count = len(new_rows)
        if i > 0 and new_count == prev_count:
            break
        prev_count = new_count

    print(f"[scraper] Collected {len(emails)} unique emails.")
    return emails


# ---------------------------------------------------------------------------
# Dialog parsing (sync, strict selectors + polling)
# ---------------------------------------------------------------------------

def _parse_full_name(full_name: str):
    parts = full_name.strip().split()
    if len(parts) >= 3:
        return parts[0], parts[1], " ".join(parts[2:])
    elif len(parts) == 2:
        return parts[0], parts[1], None
    elif len(parts) == 1:
        return parts[0], None, None
    return None, None, None


def _check_file_input_has_value(page: Page, label_text: str) -> bool:
    """
    Checks if a file upload row has a real filename in its readonly input.

    Uses: .student_info__row filter(has_text=label_text) → input.q-field__native
    """
    try:
        row = page.locator(".student_info__row").filter(has_text=label_text).first
        if row.count() == 0:
            return False
        input_el = row.locator("input.q-field__native")
        if input_el.count() == 0:
            return False
        value = input_el.get_attribute("value")
        if value is None:
            return False
        value = value.strip()
        if not value or value.lower() in ("выбрать", "select", "none", ""):
            return False
        return True
    except Exception:
        return False


def _get_file_input_value(page: Page, label_text: str) -> Optional[str]:
    """
    Returns the filename from the file input row, or None if empty/placeholder.
    """
    try:
        row = page.locator(".student_info__row").filter(has_text=label_text).first
        if row.count() == 0:
            return None
        input_el = row.locator("input.q-field__native")
        if input_el.count() == 0:
            return None
        value = input_el.get_attribute("value")
        if value is None:
            return None
        value = value.strip()
        if not value or value.lower() in ("выбрать", "select", "none", ""):
            return None
        return value
    except Exception:
        return None


def _download_file_from_row(page: Page, label_text: str) -> Optional[tuple]:
    """
    Downloads a PDF file from a student dialog row.

    Steps:
    1. Get filename from input.q-field__native
    2. Find and click the open_in_new icon in the same row
    3. Intercept the new tab
    4. Download bytes via fetch() in page.evaluate (preserves auth cookies)
    5. Close the PDF tab
    6. Return (filename, bytes)
    """
    try:
        file_name = _get_file_input_value(page, label_text)
        if not file_name:
            return None

        row = page.locator(".student_info__row").filter(has_text=label_text).first
        if row.count() == 0:
            return None

        open_icon = row.locator("i.q-icon.notranslate.material-icons.cursor-pointer").filter(has_text="open_in_new")
        if open_icon.count() == 0:
            print(f"[scraper] No open_in_new icon found for '{label_text}'")
            return None

        with page.context.expect_page() as new_page_info:
            open_icon.click()

        pdf_page = new_page_info.value
        pdf_page.wait_for_load_state("domcontentloaded", timeout=10000)

        pdf_url = pdf_page.url
        print(f"[scraper] Downloading PDF: {file_name} from {pdf_url}")

        bytes_data = page.evaluate(f"""
            async () => {{
                const response = await fetch('{pdf_url}');
                const buffer = await response.arrayBuffer();
                return Array.from(new Uint8Array(buffer));
            }}
        """)

        pdf_page.close()

        if not bytes_data:
            print(f"[scraper] Downloaded empty bytes for '{label_text}'")
            return None

        return (file_name, bytes(bytes_data))

    except Exception as e:
        print(f"[scraper] Error downloading '{label_text}': {e}")
        try:
            for p in page.context.pages:
                if p != page:
                    p.close()
        except Exception:
            pass
        return None


def _extract_field_value(page: Page, title_text: str) -> Optional[str]:
    rows = page.query_selector_all(".student_info__row")
    for row in rows:
        title_el = row.query_selector(".student_info__row-title")
        if title_el:
            title = title_el.inner_text()
            if title_text.lower() in title.strip().lower():
                value_el = row.query_selector(".student_info__row-value")
                if value_el:
                    return value_el.inner_text().strip()
                input_el = row.query_selector("input.q-field__native")
                if input_el:
                    val = input_el.get_attribute("value")
                    if val:
                        return val.strip()
                span_el = row.query_selector(".q-field__native span.ellipsis")
                if span_el:
                    return span_el.inner_text().strip()
    return None


def _parse_datetime(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    text = text.strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _map_route_status(raw_status: str) -> str:
    mapping = {
        "новое": "new",
        "на проверке": "in_progress",
        "одобрено": "approved",
        "отклонено": "rejected",
        "new": "new",
        "in_progress": "in_progress",
        "approved": "approved",
        "rejected": "rejected",
    }
    return mapping.get(raw_status.strip().lower(), "new")


def parse_student_dialog(page: Page, config: Dict[str, Any]) -> Optional[StudentData]:
    """
    Parses the open student dialog. Assumes dialog is already loaded and visible.
    Called after the main loop confirms dialog readiness via polling.
    """
    full_name_selector = "div.cursor-pointer.text-center"

    data = StudentData()

    try:
        # --- ФИО from header ---
        full_name_el = page.query_selector(full_name_selector)
        if full_name_el:
            full_name = full_name_el.inner_text().strip()
            if full_name:
                data.last_name, data.first_name, data.patronymic = _parse_full_name(full_name)

        # --- Student ID ---
        id_el = page.query_selector(".student_dialog__menu-footer__id")
        if id_el:
            data.student_id = id_el.inner_text().strip()

        # --- Email from footer ---
        email_el = page.query_selector(".student_dialog__menu-footer__email")
        if email_el:
            data.email = email_el.inner_text().strip()

        # --- Course from footer ---
        course_el = page.query_selector(".student_dialog__menu-footer__course")
        if course_el:
            data.course = course_el.inner_text().strip()

        # --- Document scans: check + download PDFs ---
        doc_configs = [
            ("Скан паспорт", "passport"),
            ("Скан документа о смене ФИО", "name_change"),
            ("Скан документа об образовании", "education"),
        ]

        for label_text, attr_prefix in doc_configs:
            has_file = _check_file_input_has_value(page, label_text)
            setattr(data, f"has_{attr_prefix}_scan", has_file)

            if has_file:
                result = _download_file_from_row(page, label_text)
                if result:
                    file_name, file_bytes = result
                    setattr(data, f"{attr_prefix}_file_name", file_name)
                    setattr(data, f"{attr_prefix}_file_bytes", file_bytes)
                    print(f"[scraper] Downloaded {label_text}: {file_name} ({len(file_bytes)} bytes)")
                else:
                    print(f"[scraper] Failed to download {label_text}")

        # --- Click "Учет" tab to access enrollment data ---
        tabs_list = page.query_selector(".tabs-list")
        if tabs_list:
            tab_items = tabs_list.query_selector_all(".q-item")
            for tab_item in tab_items:
                tab_text = tab_item.inner_text().strip()
                if tab_text == "Учет":
                    try:
                        tab_item.click()
                        page.wait_for_timeout(500)
                    except Exception:
                        pass
                    break

        # --- Enrollment documents via row filter + input value ---
        data.has_enrollment_application = _check_file_input_has_value(page, "Заявление на зачисление")
        data.has_personal_data_consent = _check_file_input_has_value(page, "Согласие на обработку персональных данных")

        # --- Route metadata ---
        status_val = _extract_field_value(page, "Статус")
        if status_val:
            data.route_status = _map_route_status(status_val)

        uploaded_val = _extract_field_value(page, "Дата подгрузки")
        if uploaded_val:
            data.uploaded_at = _parse_datetime(uploaded_val)

        operator_val = _extract_field_value(page, "Оператор")
        if operator_val:
            data.operator = operator_val

        verified_val = _extract_field_value(page, "Дата проверки")
        if verified_val:
            data.verified_at = _parse_datetime(verified_val)

        # --- Collect all raw fields for debugging ---
        rows = page.query_selector_all(".student_info__row")
        for row in rows:
            title_el = row.query_selector(".student_info__row-title")
            value_el = row.query_selector(".student_info__row-value")
            if title_el and value_el:
                title = title_el.inner_text().strip()
                value = value_el.inner_text().strip()
                if not value:
                    input_el = row.query_selector("input.q-field__native")
                    if input_el:
                        v = input_el.get_attribute("value")
                        if v:
                            value = v.strip()
                    if not value:
                        span_el = row.query_selector(".q-field__native span.ellipsis")
                        if span_el:
                            value = span_el.inner_text().strip()
                if title and value:
                    data.raw_fields[title] = value

    except Exception as e:
        print(f"[scraper] Error parsing dialog: {e}")

    return data


# ---------------------------------------------------------------------------
# Main scraper — sync with direct Django ORM calls
# ---------------------------------------------------------------------------

def run_scraper(
    config_path: str = "scraper_config.yaml",
    *,
    test_mode: bool = False,
    on_student: Optional[Callable[[StudentData], None]] = None,
) -> List[StudentData]:
    """
    Sync scraper pipeline:
    1. Login
    2. Navigate → localStorage → reload
    3. Collect unique emails
    4. For each email:
       a. cell = page.locator("table.q-table td").filter(has_text=email).first
       b. cell.scroll_into_view_if_needed()
       c. cell.click()
       d. Wait for dialog (5 attempts, 2s each)
       e. parse_student_dialog() (dialog already confirmed loaded)
       f. save_student_to_db(data)  ← direct sync Django ORM call
       g. page.keyboard.press("Escape")
       h. wait_for_selector(tbody, state='visible') + 500ms  ← dynamic table reload
    """
    # Allow sync ORM calls from Playwright's sync context
    import os
    os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

    print(f"[scraper] Loading config from {config_path!r}")
    config = load_config(config_path)

    pw, browser, context = _build_browser(config)
    page = context.new_page()

    students: List[StudentData] = []

    try:
        login(page, config)

        students_url = config.get("pages", {}).get(
            "students_page_url",
            "https://edu.firpo.ru/jM5a-1Pq8/students",
        )

        # --- Navigate ---
        print(f"[scraper] Navigating to students page: {students_url}")
        page.goto(students_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("networkidle", timeout=60000)

        # --- Inject localStorage preset ---
        inject_localstorage_preset(page, config)

        # --- Reload so Quasar picks up the new limit ---
        print("[scraper] Reloading page to apply localStorage preset...")
        page.reload(wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("networkidle", timeout=60000)
        time.sleep(2)

        # --- Collect unique emails ---
        emails = collect_unique_emails(page, config)

        # --- Test mode: limit to first 10 students ---
        if test_mode:
            print("[scraper] Running in TEST mode. Processing only the first 10 students.")
            emails = emails[:10]

        total = len(emails)

        if total == 0:
            print("[scraper] No emails collected. Stopping.")
            return students

        # --- Main cycle ---
        for idx, email in enumerate(emails):
            try:
                print(f"[scraper] [{idx + 1}/{total}] Processing student with email: {email}")

                # Step 1: Find td cell with this email directly
                cell = page.locator("table.q-table td").filter(has_text=email).first

                if cell.count() == 0:
                    print(f"[scraper] [{idx + 1}/{total}] Cell not found for email: {email}")
                    continue

                # Step 2: Scroll into view
                cell.scroll_into_view_if_needed()
                page.wait_for_timeout(300)

                # Step 3: Click the cell
                cell.click()

                # Step 4: Wait for dialog to load — exactly 5 attempts per student
                dialog_loaded = False
                for attempt in range(1, 6):
                    print(f"[scraper] [{idx + 1}/{total}] Checking dialog load. Attempt {attempt}/5...")
                    try:
                        if page.locator("div.cursor-pointer.text-center").is_visible():
                            dialog_loaded = True
                            break
                    except Exception:
                        pass
                    if attempt < 5:
                        page.wait_for_timeout(2000)

                if not dialog_loaded:
                    print(f"[scraper] [{idx + 1}/{total}] Dialog did not load after 5 attempts. Skipping this student.")
                    try:
                        page.keyboard.press("Escape")
                        page.wait_for_selector("tbody.q-virtual-scroll__content", state="visible", timeout=10000)
                        page.wait_for_timeout(500)
                    except Exception:
                        pass
                    continue

                # Step 5: Parse dialog (already confirmed loaded)
                student_data = parse_student_dialog(page, config)

                if student_data is None:
                    print(
                        f"[scraper] [{idx + 1}/{total}] "
                        f"Skipped (dialog parse returned None). Email: {email}"
                    )
                    page.keyboard.press("Escape")
                    page.wait_for_selector("tbody.q-virtual-scroll__content", state="visible", timeout=10000)
                    page.wait_for_timeout(500)
                    continue

                if student_data and student_data.first_name:
                    students.append(student_data)

                    # Step 6: Save to Django DB
                    try:
                        student_obj, route_obj, created = save_student_to_db(student_data)
                        action = "Created" if created else "Updated"
                        print(
                            f"[scraper] [{idx + 1}/{total}] "
                            f"{action} in DB: {student_data.last_name} {student_data.first_name} ({email})"
                        )
                    except Exception as db_err:
                        print(f"[scraper] [{idx + 1}/{total}] DB save error: {db_err}")

                    if on_student:
                        on_student(student_data)
                else:
                    print(
                        f"[scraper] [{idx + 1}/{total}] "
                        f"Skipped (no name in dialog). Email: {email}"
                    )

                # Step 7: Close dialog via Escape
                page.keyboard.press("Escape")

                # Step 8: Dynamic wait for table to reload
                print("[scraper] Waiting for virtual scroll content to reload and stabilize...")
                page.wait_for_selector(
                    "tbody.q-virtual-scroll__content",
                    state="visible",
                    timeout=10000,
                )
                page.wait_for_timeout(500)

            except Exception as e:
                print(f"[scraper] [{idx + 1}/{total}] Error: {e}")
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(5000)
                except Exception:
                    pass
                continue

    finally:
        browser.close()
        pw.stop()

    print(f"[scraper] Done. Total students parsed: {len(students)}")
    return students


def run_scraper_single(
    config_path: str = "scraper_config.yaml",
    student_id: Optional[str] = None,
    *,
    on_student: Optional[Callable[[StudentData], None]] = None,
) -> Optional[StudentData]:
    """
    Scrape a single student by ID or the first email.
    """
    # Allow sync ORM calls from Playwright's sync context
    import os
    os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

    print(f"[scraper] Loading config from {config_path!r}")
    config = load_config(config_path)

    pw, browser, context = _build_browser(config)
    page = context.new_page()

    try:
        login(page, config)

        students_url = config.get("pages", {}).get(
            "students_page_url",
            "https://edu.firpo.ru/jM5a-1Pq8/students",
        )

        page.goto(students_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("networkidle", timeout=60000)

        inject_localstorage_preset(page, config)

        print("[scraper] Reloading page to apply localStorage preset...")
        page.reload(wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("networkidle", timeout=60000)
        time.sleep(2)

        emails = collect_unique_emails(page, config)

        if student_id:
            target_email = None
            for email in emails:
                cell = page.locator("table.q-table td").filter(has_text=email).first
                if cell.count() > 0:
                    parent = cell.locator("..")
                    row_text = parent.inner_text()
                    if student_id in row_text:
                        target_email = email
                        break
            if target_email is None:
                row_locator = page.locator("table.q-table tr").filter(has_text=student_id)
                if row_locator.count() > 0:
                    row_text = row_locator.first.inner_text()
                    match = _EMAIL_RE.search(row_text)
                    if match:
                        target_email = match.group(0)
            if target_email is None:
                target_email = emails[0] if emails else None
            if target_email is None:
                print(f"[scraper] Student ID {student_id} not found in table.")
                return None
        else:
            if not emails:
                print("[scraper] No rows found in table.")
                return None
            target_email = emails[0]

        print(f"[scraper] Processing student with email: {target_email}")

        cell = page.locator("table.q-table td").filter(has_text=target_email).first
        if cell.count() == 0:
            print(f"[scraper] Cell not found for email: {target_email}")
            return None

        cell.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        cell.click()

        # Wait for dialog to load — exactly 5 attempts
        dialog_loaded = False
        for attempt in range(1, 6):
            print(f"[scraper] Checking dialog load. Attempt {attempt}/5...")
            try:
                if page.locator("div.cursor-pointer.text-center").is_visible():
                    dialog_loaded = True
                    break
            except Exception:
                pass
            if attempt < 5:
                page.wait_for_timeout(2000)

        if not dialog_loaded:
            print("[scraper] Dialog did not load after 5 attempts.")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            return None

        student_data = parse_student_dialog(page, config)

        if student_data is None:
            print("[scraper] Dialog did not load.")
            return None

        if student_data and student_data.first_name:
            try:
                student_obj, route_obj, created = save_student_to_db(student_data)
                action = "Created" if created else "Updated"
                print(f"[scraper] {action} in DB: {student_data.last_name} {student_data.first_name}")
            except Exception as db_err:
                print(f"[scraper] DB save error: {db_err}")

            if on_student:
                on_student(student_data)

        page.keyboard.press("Escape")

        print("[scraper] Waiting for virtual scroll content to reload and stabilize...")
        page.wait_for_selector("tbody.q-virtual-scroll__content", state="visible", timeout=10000)
        page.wait_for_timeout(500)

        return student_data

    finally:
        browser.close()
        pw.stop()
