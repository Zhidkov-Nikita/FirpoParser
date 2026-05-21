# FIRPO Parser — Скрапер платформы ИРПО

Django-проект для автоматизированного парсинга платформы ИРПО (https://edu.firpo.ru/) с сохранением данных о слушателях и их маршрутах обучения в PostgreSQL.

---

## Архитектура скрапера

### Стек технологий

| Компонент | Технология | Назначение |
|-----------|-----------|------------|
| Браузерная автоматизация | **Playwright** | SPA-парсинг Quasar Framework, виртуальный скролл, диалоги |
| Фреймворк | **Django 5.2** | ORM, админка, management commands |
| Асинхронные задачи | **Celery 5** + **Redis** | Фоновый запуск скрапера по расписанию |
| Конфигурация | **PyYAML** | Селекторы, URL, учётные данные |

### Почему Playwright, а не Selenium?

Целевая платформа — SPA на **Quasar Framework** с виртуальным скроллингом таблицы. Playwright обеспечивает:
- Нативную поддержку ожидания динамического рендеринга
- Стабильную работу с виртуальными списками
- Более быстрый и надёжный API для кликов и парсинга

### Точки входа

```
parser/scraper.py          # Ядро скрапера — Playwright + парсинг диалогов
parser/services.py         # Пайплайн сохранения — update_or_create в БД
parser/tasks.py            # Celery-задачи для фонового запуска
parser/management/commands/
    run_scraper.py         # CLI: python manage.py run_scraper (все студенты)
    run_scraper_single.py  # CLI: python manage.py run_scraper_single --student-id=ID
parser/models.py           # Student, EnrollmentRoute
parser/admin.py            # Регистрация моделей в админке
scraper_config.yaml        # Конфигурация (gitignored)
scraper_config.example.yaml # Шаблон конфигурации
```

### Пайплайн работы

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         run_scraper()                                     │
│                                                                          │
│  1. load_config()          ← Загрузка scraper_config.yaml                │
│  2. _build_browser()       ← Запуск Playwright (chromium headless)       │
│  3. login()                ← Авторизация на edu.firpo.ru                 │
│  4. page.goto(students)    ← Переход на /jM5a-1Pq8/students              │
│  5. scroll_table_to_end()  ← Прокрутка виртуальной таблицы до конца      │
│  6. for each row:                                                         │
│       a. row.click()       ← Клик на строку → открытие диалога           │
│       b. Ожидание диалога  ← 5 попыток × 2с (div.cursor-pointer)        │
│          └─ Если не загрузился → Escape → continue (следующий студент)   │
│       c. parse_student_dialog()                                           │
│          ├─ ФИО из хедера  (div.cursor-pointer.text-center)              │
│          ├─ ID, email      (.student_dialog__menu-footer__*)             │
│          ├─ Сканы док-тов  ← Проверка input.q-field__native              │
│          │   └─ Если файл есть → клик open_in_new → новая вкладка        │
│          │       → fetch() через page.evaluate → ContentFile → DB        │
│          ├─ Клик «Учет»    (вторая вкладка tabs-list)                    │
│          ├─ Статусы док-тов (Заявление, Согласие — по input value)       │
│          └─ Метаданные     (Статус, Оператор, Даты)                      │
│       d. save_student_to_db() → update_or_create + FileField.save()      │
│       e. Закрыть диалог (Escape)                                         │
│       f. wait_for_selector(tbody, visible) + 500мс ← ожидание таблицы    │
│  7. browser.close()                                                       │
└──────────────────────────────────────────────────────────────────────────┘
```

### Структура диалога (Quasar)

```
div[class*="q-portal--dialog"]          ← Модальное окно
├── .student_dialog__header-info
│   └── .cursor-pointer                 ← "Иванов Иван Иванович"
├── .student_dialog__menu-footer
│   ├── .student_dialog__menu-footer__id     ← "206851"
│   ├── .student_dialog__menu-footer__email  ← "email@mail.ru"
│   └── .student_dialog__menu-footer__course ← Название курса
├── .tabs-list                         ← Вкладки навигации
│   ├── "Обучаемый" (активна по умолчанию)
│   ├── "Учет" (клик → панель документов)
│   ├── "Обучение"
│   ├── "Контакты"
│   ├── "Аккаунт"
│   └── "Действия"
└── .q-tab-panels
    └── .student_info__row             ← Строки данных
        ├── .student_info__row-title   ← "Фамилия*"
        ├── .student_info__row-value   ← "Иванов"
        └── img[src*="square-check"]   ← Документ подтверждён
            img[src*="square-x"]       ← Документ отсутствует
```

---

## Схема интеграции с БД

### Модели

```
Student (1) ─── OneToOne ─── (1) EnrollmentRoute
```

### Маппинг данных из диалога → поля моделей

#### Student

| Поле модели | Источник в диалоге | Логика извлечения |
|-------------|-------------------|-------------------|
| `last_name` | `.student_dialog__header-info .cursor-pointer` | Первое слово из полного ФИО |
| `first_name` |同上 | Второе слово из полного ФИО |
| `patronymic` |同上 | Остальные слова (если есть) |
| `passport_file` | Строка "Скан паспорт*" → `input.q-field__native` | Скачивание PDF через новую вкладку (`open_in_new` icon) → `fetch()` в `page.evaluate` → `ContentFile` |
| `name_change_file` | Строка "Скан документа о смене ФИО" → `input.q-field__native` | Аналогично `passport_file` |
| `education_file` | Строка "Скан документа об образовании" → `input.q-field__native` | Аналогично `passport_file` |

#### EnrollmentRoute

| Поле модели | Источник в диалоге | Логика извлечения |
|-------------|-------------------|-------------------|
| `student` | — | OneToOne через update_or_create |
| `has_enrollment_application` | Вкладка "Учет" → "Заявление на зачисление" → `square-check` | Есть иконка ✓ → True |
| `has_personal_data_consent` | Вкладка "Учет" → "Согласие на обработку персональных данных" → `square-check` | Есть иконка ✓ → True |
| `status` | Вкладка "Учет" → строка "Статус" | Маппинг: "Новое"→new, "На проверке"→in_progress, "Одобрено"→approved, "Отклонено"→rejected |
| `uploaded_at` | Вкладка "Учет" → строка "Дата подгрузки" | Парсинг dd.mm.yyyy HH:MM |
| `operator` | Вкладка "Учет" → строка "Оператор" | Прямое текстовое значение |
| `verified_at` | Вкладка "Учет" → строка "Дата проверки" | Парсинг dd.mm.yyyy HH:MM |

### Статусы (StatusChoices)

| Текст в интерфейсе | StatusChoices | Display |
|-------------------|---------------|---------|
| `Новое` | `new` | Новое |
| `На проверке` | `in_progress` | На проверке |
| `Одобрено` | `approved` | Одобрено |
| `Отклонено` | `rejected` | Отклонено |

### Идемпотентность (update_or_create)

Lookup-ключ для `Student`: `(last_name, first_name)`. При повторном запуске:
- Если студент найден — обновляются все поля (сканы, статусы, оператор, даты)
- Если студент не найден — создаётся новая пара Student + EnrollmentRoute
- `EnrollmentRoute` привязывается через `OneToOneField(student=student)` и также обновляется через `update_or_create`

---

## Инструкция по запуску и кастомизации

### 1. Установка зависимостей

```bash
# Создание виртуального окружения
python3 -m venv venv
source venv/bin/activate

# Установка Python-зависимостей
pip install -r requirements.txt

# Установка системного Chromium (НЕ нужен playwright install)
sudo apt update && sudo apt install -y chromium-browser
```

### 2. Настройка конфигурации

```bash
cp scraper_config.example.yaml scraper_config.yaml
```

Откройте `scraper_config.yaml` и укажите реальные данные:

```yaml
auth:
  login_url: "https://edu.firpo.ru/admin-login"
  username: "your_email@example.com"   # Ваш логин
  password: "your_password"            # Ваш пароль
  username_selector: "input[name='email']"
  password_selector: "input[name='password']"
  submit_selector: "button[type='submit']"
  success_selector: ".q-page"

pages:
  students_page_url: "https://edu.firpo.ru/jM5a-1Pq8/students"

table:
  scroll_container_selector: "tbody.q-virtual-scroll__content"
  row_selector: "tbody.q-virtual-scroll__content tr"
  max_scroll_iterations: 50
  scroll_delay: 0.5

dialog:
  selector: "div[class*='q-portal--dialog']"
  timeout: 15000

browser:
  # Путь до системного Chromium (обязательно)
  binary_path: "/usr/bin/chromium-browser"
  headless: true
```

> **Важно:** Playwright использует системный Chromium через `executable_path`. Команда `playwright install` **не требуется**. Убедитесь, что chromium-browser установлен: `sudo apt install chromium-browser`.

### 3. Применение миграций

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 4. Запуск парсинга

#### Все студенты:

```bash
python manage.py run_scraper
# или с кастомным конфигом:
python manage.py run_scraper --config /path/to/config.yaml
```

#### Тестовый режим (первые 10 записей):

```bash
python manage.py run_scraper --test
```

Флаг `--test` ограничивает обработку первыми 10 студентами из таблицы. Удобно для отладки и проверки конфигурации перед полным запуском.

#### Один студент по ID:

```bash
python manage.py run_scraper_single --student-id=206851
# Без ID — берёт первую строку таблицы:
python manage.py run_scraper_single
```

#### Через Celery (фоновый режим):

```bash
# Запуск worker
celery -A firdo_parser worker -l info

# Из Django shell:
python manage.py shell
>>> from parser.tasks import run_scraper_task
>>> run_scraper_task.delay()
```

### 5. Запуск Django-сервера

```bash
python manage.py runserver
```

Админка: `http://localhost:8000/` (корневой URL → `/admin/`).

---

## Кастомизация

### Изменение селекторов (если обновился интерфейс)

**Файл:** `scraper_config.yaml`

| Ключ | Что делает |
|------|-----------|
| `auth.*_selector` | Селекторы формы авторизации |
| `pages.students_page_url` | URL страницы слушателей |
| `table.scroll_container_selector` | Контейнер виртуального скролла |
| `table.row_selector` | Селектор строки таблицы |
| `dialog.selector` | Селектор модального окна (частичное совпадение) |
| `dialog.timeout` | Таймаут ожидания диалога (мс) |

### Изменение маппинга статусов

**Файл:** `parser/scraper.py`, функция `_map_route_status()`

```python
def _map_route_status(raw_status: str) -> str:
    mapping = {
        "новое": "new",
        "на проверке": "in_progress",
        "одобрено": "approved",
        "отклонено": "rejected",
    }
    return mapping.get(raw_status.strip().lower(), "new")
```

### Изменение логики скачивания документов

**Файл:** `parser/scraper.py`, функции `_check_file_input_has_value()`, `_download_file_from_row()`

Скачивание PDF работает по следующему алгоритму:

1. Проверка `input.q-field__native` — если значение "Выбрать" или пустое, файл отсутствует
2. Поиск иконки `open_in_new` внутри той же строки `.student_info__row`
3. Клик по иконке с перехватом новой вкладки через `page.context.expect_page()`
4. Получение URL PDF из `pdf_page.url`
5. Скачивание байтов через `fetch()` внутри `page.evaluate()` (сохраняет куки авторизации)
6. Закрытие PDF-вкладки: `pdf_page.close()`
7. Сохранение в Django через `student.passport_file.save(name, ContentFile(bytes))`

### Извлечение дополнительных полей

**Файл:** `parser/scraper.py`, функция `parse_student_dialog()`

Все поля из вкладки "Обучаемый" сохраняются в `data.raw_fields` как dict. Для добавления нового поля в модель:

1. Добавьте поле в `parser/models.py`
2. `python manage.py makemigrations && python manage.py migrate`
3. В `parse_student_dialog()` извлеките значение через `_extract_field_value(dialog, "Название поля")`
4. В `services.py` добавьте поле в `defaults={}` для `update_or_create`

### Добавление нового источника данных

Если нужно парсить данные из других вкладок ("Обучение", "Контакты", "Аккаунт"):

1. В `parse_student_dialog()` кликните на нужную вкладку:
   ```python
   for tab_item in tabs_list.query_selector_all(".q-item"):
       if tab_item.inner_text().strip() == "Обучение":
           tab_item.click()
           page.wait_for_timeout(500)
           break
   ```
2. Извлеките данные аналогично `_extract_field_value()`
3. Добавьте новые поля в модели и сервисы

---

## Структура проекта

```
FIRPO parser/
├── firdo_parser/             # Django project package
│   ├── settings.py           # Настройки проекта
│   ├── urls.py               # URLs (корень → /admin/)
│   ├── celery.py             # Celery app config
│   ├── wsgi.py / asgi.py
│
├── parser/                   # Приложение парсера
│   ├── models.py             # Student, EnrollmentRoute
│   ├── admin.py              # Регистрация моделей в админке
│   ├── scraper.py            # Playwright-скрапер (парсинг Quasar SPA)
│   ├── services.py           # Сохранение: update_or_create в БД
│   ├── tasks.py              # Celery-задачи
│   ├── apps.py
│   └── management/commands/
│       ├── run_scraper.py        # CLI: все студенты
│       └── run_scraper_single.py # CLI: один студент по ID
│
├── scraper_config.yaml       # Конфиг скрапера (gitignored)
├── scraper_config.example.yaml # Шаблон конфига
├── example.html              # Пример HTML диалогового окна
├── requirements.txt
├── manage.py
└── README.md                 # Этот файл
```
