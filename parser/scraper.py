"""
Playwright-based scraper for FIRPO Edu platform (Quasar SPA).

Target: https://edu.firpo.ru/jM5a-1Pq8/students
Extracts student personal info, downloads PDF documents, and parses
the "Маршрут" tab for enrollment route data.

Uses synchronous Playwright API (sync_api) + direct Django ORM calls.
"""

import re
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import yaml
from playwright.sync_api import (
    sync_playwright,
    Page,
    BrowserContext,
    TimeoutError as PwTimeoutError,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RouteDocumentData:
    """Data extracted from a single row in the Маршрут table."""
    name: str = ""
    is_checked: bool = False
    date_text: Optional[str] = None
    operator: Optional[str] = None
    status_value: Optional[Union[bool, str]] = None


@dataclass
class StudentData:
    """Raw data extracted from a single student dialog."""
    student_id: Optional[str] = None
    email: Optional[str] = None
    course: Optional[str] = None
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    patronymic: Optional[str] = None

    passport_file_name: Optional[str] = None
    passport_file_bytes: Optional[bytes] = None

    name_change_file_name: Optional[str] = None
    name_change_file_bytes: Optional[bytes] = None

    education_file_name: Optional[str] = None
    education_file_bytes: Optional[bytes] = None

    route_documents: List[RouteDocumentData] = field(default_factory=list)
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
# Django ORM save
# ---------------------------------------------------------------------------

ROUTE_MODEL_MAPPING = {
    "Поступление на курс": "CourseEnrollment",
    "Согласие на обработку персональных данных": "PersonalDataConsent",
    "Заявление на зачисление": "EnrollmentApplication",
    "Договор на обучение": "EducationContract",
    "Первичные документы": "PrimaryDocuments",
}


def save_student_to_db(data: StudentData):
    """
    Saves Student + all 5 route document models to Django DB.
    Handles FileField for document scans via ContentFile.
    """
    from django.core.files.base import ContentFile
    from django.db import transaction
    from parser.models import (
        Student,
        CourseEnrollment,
        PersonalDataConsent,
        EnrollmentApplication,
        EducationContract,
        PrimaryDocuments,
    )

    MODEL_CLASS_MAP = {
        "CourseEnrollment": CourseEnrollment,
        "PersonalDataConsent": PersonalDataConsent,
        "EnrollmentApplication": EnrollmentApplication,
        "EducationContract": EducationContract,
        "PrimaryDocuments": PrimaryDocuments,
    }

    if not data.first_name or not data.last_name:
        raise ValueError(
            f"Cannot save without name: last_name={data.last_name!r}, first_name={data.first_name!r}"
        )

    with transaction.atomic():
        student, student_created = Student.objects.update_or_create(
            last_name=data.last_name,
            first_name=data.first_name,
            defaults={
                "patronymic": data.patronymic,
                "student_id": data.student_id or "",
                "email": data.email or "",
                "course": data.course or "",
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

        for route_doc in data.route_documents:
            model_name = ROUTE_MODEL_MAPPING.get(route_doc.name)
            if not model_name:
                print(f"[scraper] Unknown route document: {route_doc.name}")
                continue

            model_cls = MODEL_CLASS_MAP.get(model_name)
            if not model_cls:
                print(f"[scraper] No model class for: {model_name}")
                continue

            model_cls.objects.update_or_create(
                student=student,
                defaults={
                    "is_checked": route_doc.is_checked,
                    "date_text": route_doc.date_text,
                    "operator": route_doc.operator,
                },
            )

    return student, student_created


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
# Dialog parsing helpers
# ---------------------------------------------------------------------------

def _parse_full_name(full_name: str):
    parts = full_name.strip().split()
    last_name = parts[0] if len(parts) > 0 else "Не указано"
    first_name = parts[1] if len(parts) > 1 else ""
    patronymic = " ".join(parts[2:]) if len(parts) > 2 else ""
    return last_name, first_name, patronymic


def _check_file_input_has_value(page: Page, label_text: str) -> bool:
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
        traceback.print_exc()
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


# ---------------------------------------------------------------------------
# Маршрут tab parsing
# ---------------------------------------------------------------------------

def _switch_to_route_tab(page: Page, email: str = "") -> bool:
    """
    Clicks the second tab (index 1) to switch to "Маршрут".
    Waits up to 5 attempts (1s each) for the tab to be selected.
    """
    tab_selector = (
        "div.q-item.q-item-type.row.no-wrap.q-item--clickable"
        ".q-link.cursor-pointer.q-focusable.q-hoverable.col.row.items-center.tabs-item.q-pa-md"
    )

    tabs = page.query_selector_all(tab_selector)
    print(f"[DEBUG] Найдено табов: {len(tabs)}")
    if len(tabs) < 2:
        print(f"[DEBUG] Недостаточно табов для переключения на Маршрут (найдено: {len(tabs)})")
        for i, tab in enumerate(tabs):
            try:
                txt = tab.inner_text().strip()
                print(f"[DEBUG]   Таб {i}: '{txt}'")
            except Exception:
                print(f"[DEBUG]   Таб {i}: <не удалось прочитать текст>")
        return False

    print(f"[DEBUG] Кликаю на второй таб (Маршрут) для студента {email}...")
    try:
        tabs[1].click()
        print("[DEBUG] Клик выполнен")
    except Exception as e:
        print(f"[DEBUG] Ошибка клика по второму табу: {e}")
        return False

    page.wait_for_timeout(500)

    for attempt in range(1, 6):
        try:
            selected_items = page.query_selector_all("div.student_horizontal__tabs-item.selected")
            print(f"[DEBUG] Попытка {attempt}: найдено selected элементов: {len(selected_items)}")
            for item in selected_items:
                text = item.inner_text().strip()
                print(f"[DEBUG]   Selected элемент текст: '{text}'")
                if "маршрут" in text.lower():
                    print(f"[DEBUG] Успешно переключено! Текст активного таба: '{text}'")
                    return True
        except Exception as e:
            print(f"[DEBUG] Попытка {attempt}: ошибка чтения selected элементов: {e}")

        if attempt < 5:
            page.wait_for_timeout(1000)

    print("[DEBUG] Маршрут tab не стал активным после 5 попыток")
    return False


def _parse_status_cell(td) -> Optional[Union[bool, str]]:
    """
    Универсальный сбор статуса из ячейки (2-й столбец).
    Возвращает True/False/"X"/None.
    """
    try:
        img = td.locator("img").first
        if img.count() > 0:
            src = img.get_attribute("src") or ""
            src_lower = src.lower()
            if "square-check" in src_lower:
                return True
            if "square-x" in src_lower or "square-close" in src_lower:
                return "X"
            return False

        toggle = td.locator(".q-toggle").first
        if toggle.count() > 0:
            aria = toggle.get_attribute("aria-checked")
            if aria == "true":
                return True
            return False

        text = td.text_content()
        if text:
            text = text.strip()
            if "%" in text:
                try:
                    pct = int(text.replace("%", "").strip())
                    return pct > 0
                except (ValueError, TypeError):
                    return False
        return False
    except Exception:
        return False


def _parse_route_table(page: Page, student_id: str = "") -> List[RouteDocumentData]:
    """
    Парсит ВСЕ строки таблицы Маршрут с универсальным определением статуса.
    Возвращает список RouteDocumentData для сохранения в БД.
    """
    results: List[RouteDocumentData] = []

    try:
        rows = page.locator("tbody.q-virtual-scroll__content tr")
        rows_count = rows.count()
        print(f"[DEBUG] Всего tr найдено во внутреннем скролле: {rows_count}")

        if rows_count == 0:
            print("[DEBUG] Нет строк в таблице маршрута")
            return results

        for idx in range(rows_count):
            print(f"--- Обработка строки {idx + 1} из {rows_count} ---")
            try:
                row = rows.nth(idx)

                # Столбец 1: Название документа
                name_el = row.locator("td:nth-child(1) div.student_table__field-value")
                doc_title = name_el.text_content().strip() if name_el.count() > 0 else ""
                print(f"[DEBUG] Столбец 1 (Название): '{doc_title}'")

                if not doc_title:
                    print(f"[DEBUG] Пустое название документа, пропускаю строку {idx + 1}")
                    continue

                # Столбец 2: Универсальный сбор статуса
                td2 = row.locator("td:nth-child(2)")
                status_value = _parse_status_cell(td2)
                is_checked = status_value is True
                print(f"[DEBUG] Столбец 2 (статус): status_value={status_value!r}, is_checked={is_checked}")

                # Столбец 3: Дата
                date_el = row.locator("td:nth-child(3) div.student_table__field-value")
                date_val = date_el.text_content().strip() if date_el.count() > 0 else ""
                print(f"[DEBUG] Столбец 3 (Дата): '{date_val}'")

                # Столбец 4: Оператор
                op_el = row.locator("td:nth-child(4) div.student_table__field-value")
                operator_val = op_el.text_content().strip() if op_el.count() > 0 else ""
                print(f"[DEBUG] Столбец 4 (Оператор): '{operator_val}'")

                doc_data = RouteDocumentData(
                    name=doc_title,
                    is_checked=is_checked,
                    date_text=date_val or None,
                    operator=operator_val or None,
                    status_value=status_value,
                )
                results.append(doc_data)
                print(f"[DEBUG] Добавлен документ: {doc_title} (is_checked={is_checked})")

            except Exception as e:
                print(f"[ERROR] Ошибка на строке {idx + 1}: {e}")
                traceback.print_exc()
                continue

    except Exception as e:
        print(f"[scraper] Error parsing route table: {e}")
        traceback.print_exc()

    print(f"[DEBUG] Всего распарсенных документов маршрута: {len(results)}")
    return results


def _build_route_steps(route_docs: List[RouteDocumentData]) -> Dict[str, Dict]:
    steps = {}
    for doc in route_docs:
        steps[doc.name] = {
            "value": doc.status_value if doc.status_value is not None else doc.is_checked,
            "operator": doc.operator or "",
        }
    return steps


def _compute_student_status(route_docs: List[RouteDocumentData]) -> str:
    route_steps = _build_route_steps(route_docs)

    # --- 1. КРИТИЧЕСКИЕ И СИСТЕМНЫЕ МАРКЕРЫ ---
    expelled = route_steps.get("Отчислен", {}).get("value")
    if expelled == "X" or expelled is True:
        return "Услуга прекращена"

    if route_steps.get("Выпуск", {}).get("value") is True:
        return "Прошёл ИА"

    if route_steps.get("Зачислен на курс", {}).get("value") is True:
        return "Обучается"

    # --- 2. ЛИНЕЙНАЯ ЦЕПОЧКА ДОКУМЕНТОВ (снизу вверх) ---
    contract = route_steps.get("Договор на обучение", {})
    if contract.get("value") is True:
        operator_text = str(contract.get("operator", ""))
        if "3-х" in operator_text or "трех" in operator_text.lower():
            return "Заключен 3-х сторонний"
        return "Заключен договор"

    if route_steps.get("Согласие на обработку персональных данных.", {}).get("value") is True:
        return "Подписан СЭП"

    if route_steps.get("Заявление на зачисление", {}).get("value") is True:
        return "Заявка одобрена"

    if route_steps.get("Поступление на курс", {}).get("value") is True:
        return "Новая заявка"

    return "Статус не определен"


# ---------------------------------------------------------------------------
# Main dialog parser
# ---------------------------------------------------------------------------

def parse_student_dialog(page: Page, config: Dict[str, Any]) -> Optional[StudentData]:
    """
    Parses the open student dialog:
    1. Main tab: ФИО, ID, email, course, document scans + PDF downloads
    2. Switches to Маршрут tab, parses route table
    3. Returns StudentData with all extracted info
    """
    full_name_selector = "div.cursor-pointer.text-center"
    data = StudentData()

    try:
        # --- Main tab: basic info ---
        full_name_el = page.query_selector(full_name_selector)
        if full_name_el:
            full_name = full_name_el.inner_text().strip()
            if full_name:
                data.last_name, data.first_name, data.patronymic = _parse_full_name(full_name)

        id_el = page.query_selector(".student_dialog__menu-footer__id")
        if id_el:
            data.student_id = id_el.inner_text().strip()

        email_el = page.query_selector(".student_dialog__menu-footer__email")
        if email_el:
            data.email = email_el.inner_text().strip()

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

            if has_file:
                result = _download_file_from_row(page, label_text)
                if result:
                    file_name, file_bytes = result
                    setattr(data, f"{attr_prefix}_file_name", file_name)
                    setattr(data, f"{attr_prefix}_file_bytes", file_bytes)
                    print(f"[scraper] Downloaded {label_text}: {file_name} ({len(file_bytes)} bytes)")
                else:
                    print(f"[scraper] Failed to download {label_text}")

        # --- Switch to Маршрут tab and parse route table ---
        if _switch_to_route_tab(page, email=data.email or ""):
            page.wait_for_timeout(500)
            data.route_documents = _parse_route_table(page, student_id=data.student_id or "")
        else:
            print("[scraper] Could not switch to Маршрут tab, skipping route parsing")

        # --- Collect raw fields for debugging ---
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
        traceback.print_exc()

    return data


# ---------------------------------------------------------------------------
# Main scraper
# ---------------------------------------------------------------------------

def _close_dialog_and_wait(page: Page):
    """Closes dialog and waits for main table to stabilize."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_selector(
            "tbody.q-virtual-scroll__content",
            state="visible",
            timeout=10000,
        )
        page.wait_for_timeout(500)
    except Exception:
        pass


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
       a. Click cell → open dialog
       b. Wait for dialog (5 attempts × 2s)
       c. Parse main tab + download PDFs
       d. Switch to Маршрут tab → parse route table
       e. save_student_to_db(data)
       f. Escape → wait for table reload
    """
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

        print(f"[scraper] Navigating to students page: {students_url}")
        page.goto(students_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("networkidle", timeout=60000)

        inject_localstorage_preset(page, config)

        print("[scraper] Reloading page to apply localStorage preset...")
        page.reload(wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("networkidle", timeout=60000)
        time.sleep(2)

        emails = collect_unique_emails(page, config)

        if test_mode:
            print("[scraper] Running in TEST mode. Processing only the first 10 students.")
            emails = emails[:10]

        total = len(emails)

        if total == 0:
            print("[scraper] No emails collected. Stopping.")
            return students

        for idx, email in enumerate(emails):
            try:
                print(f"[scraper] [{idx + 1}/{total}] Processing student with email: {email}")

                cell = page.locator("table.q-table td").filter(has_text=email).first

                if cell.count() == 0:
                    print(f"[scraper] [{idx + 1}/{total}] Cell not found for email: {email}")
                    continue

                cell.scroll_into_view_if_needed()
                page.wait_for_timeout(300)
                cell.click()

                # Wait for dialog — exactly 5 attempts per student
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
                    print(f"[scraper] [{idx + 1}/{total}] Dialog did not load after 5 attempts. Skipping.")
                    _close_dialog_and_wait(page)
                    continue

                student_data = parse_student_dialog(page, config)

                if student_data is None:
                    print(f"[scraper] [{idx + 1}/{total}] Skipped (dialog parse returned None).")
                    _close_dialog_and_wait(page)
                    continue

                if student_data and student_data.first_name:
                    students.append(student_data)

                    try:
                        student_obj, created = save_student_to_db(student_data)
                        action = "Created" if created else "Updated"
                        print(
                            f"[scraper] [{idx + 1}/{total}] "
                            f"{action} in DB: {student_data.last_name} {student_data.first_name} ({email})"
                        )

                        status_text = _compute_student_status(student_data.route_documents)
                        student_obj.status = status_text
                        student_obj.save(update_fields=['status'])
                        print(f"[scraper] [{idx + 1}/{total}] Status: {status_text}")
                    except Exception as db_err:
                        print(f"[scraper] [{idx + 1}/{total}] DB save error: {db_err}")
                        traceback.print_exc()

                    if on_student:
                        on_student(student_data)
                else:
                    print(f"[scraper] [{idx + 1}/{total}] Skipped (no name in dialog).")

                _close_dialog_and_wait(page)

            except Exception as e:
                print(f"[scraper] [{idx + 1}/{total}] Critical error: {e}")
                traceback.print_exc()
                _close_dialog_and_wait(page)
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
    """Scrape a single student by ID or the first email."""
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
            print("[scraper] Dialog parse returned None.")
            _close_dialog_and_wait(page)
            return None

        if student_data and student_data.first_name:
            try:
                student_obj, created = save_student_to_db(student_data)
                action = "Created" if created else "Updated"
                print(f"[scraper] {action} in DB: {student_data.last_name} {student_data.first_name}")

                status_text = _compute_student_status(student_data.route_documents)
                student_obj.status = status_text
                student_obj.save(update_fields=['status'])
                print(f"[scraper] Status: {status_text}")
            except Exception as db_err:
                print(f"[scraper] DB save error: {db_err}")

            if on_student:
                on_student(student_data)

        _close_dialog_and_wait(page)

        return student_data

    finally:
        browser.close()
        pw.stop()
