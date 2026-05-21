from django.db import models
from django.utils.timezone import now


class Student(models.Model):
    """
    Модель Обучаемого и его личных сведений (наличие/отсутствие документов).
    """
    # Личные сведения
    first_name = models.CharField('Имя', max_length=150)
    last_name = models.CharField('Фамилия', max_length=150)
    patronymic = models.CharField('Отчество', max_length=150, blank=True, null=True)
    
    # Скан-документы (FileField для физического хранения файлов)
    passport_file = models.FileField(
        'Скан паспорта',
        upload_to='passports/',
        blank=True,
        null=True,
        help_text='Файл скана паспорта'
    )
    name_change_file = models.FileField(
        'Скан документа о смене ФИО',
        upload_to='name_changes/',
        blank=True,
        null=True,
        help_text='Файл скана документа о смене ФИО'
    )
    education_file = models.FileField(
        'Скан документа об образовании',
        upload_to='education/',
        blank=True,
        null=True,
        help_text='Файл скана документа об образовании'
    )

    created_at = models.DateTimeField('Дата создания записи', auto_now_add=True)
    updated_at = models.DateTimeField('Дата обновления записи', auto_now=True)

    class Meta:
        verbose_name = 'Обучаемый'
        verbose_name_plural = 'Обучаемые'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.last_name} {self.first_name} {self.patronymic or ''}".strip()


class EnrollmentRoute(models.Model):
    """
    Модель Учет -> Маршруты.
    Хранит информацию о заявлениях, документах и статусах их проверки.
    """
    class StatusChoices(models.TextChoices):
        NEW = 'new', 'Новое'
        IN_PROGRESS = 'in_progress', 'На проверке'
        APPROVED = 'approved', 'Одобрено'
        REJECTED = 'rejected', 'Отклонено'

    student = models.OneToOneField(
        Student,
        on_delete=models.CASCADE,
        related_name='route',
        verbose_name='Обучаемый'
    )
    
    # Первичные документы (Логические флаги подгрузки/наличия в маршруте)
    has_enrollment_application = models.BooleanField(
        'Заявление на зачисление', 
        default=False
    )
    has_personal_data_consent = models.BooleanField(
        'Согласие на обработку ПДн', 
        default=False
    )
    
    # Данные проверки и логистики
    status = models.CharField(
        'Статус',
        max_length=20,
        choices=StatusChoices.choices,
        default=StatusChoices.NEW,
        db_index=True  # Индекс для быстрой фильтрации в админке по статусам
    )
    uploaded_at = models.DateTimeField(
        'Дата и время подгрузки',
        default=now,
        db_index=True  # Индекс для сортировки по времени загрузки парсером
    )
    operator = models.CharField(
        'Оператор проверки',
        max_length=150,
        blank=True,
        null=True,
        help_text='ФИО или ID оператора, проверившего документы'
    )
    verified_at = models.DateTimeField(
        'Дата и время проверки',
        blank=True,
        null=True
    )

    class Meta:
        verbose_name = 'Маршрут учета'
        verbose_name_plural = 'Маршруты учета'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"Маршрут для {self.student} (Статус: {self.get_status_display()})"
