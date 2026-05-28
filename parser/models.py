from django.db import models


class Student(models.Model):
    """
    Модель Обучаемого и его личных сведений.
    """
    student_id = models.CharField(
        'ID Студента',
        max_length=50,
        default='',
        db_index=True,
        help_text='Уникальный ID студента из системы FIRPO'
    )
    first_name = models.CharField('Имя', max_length=150)
    last_name = models.CharField('Фамилия', max_length=150)
    patronymic = models.CharField('Отчество', max_length=150, blank=True, null=True)
    email = models.EmailField('Email', blank=True, null=True)
    course = models.CharField('Курс', max_length=255, blank=True, null=True)
    status = models.CharField('Текущий статус', max_length=100, blank=True, null=True, db_index=True)

    passport_file = models.FileField(
        'Скан паспорта',
        upload_to='passports/',
        blank=True,
        null=True,
    )
    name_change_file = models.FileField(
        'Скан документа о смене ФИО',
        upload_to='name_changes/',
        blank=True,
        null=True,
    )
    education_file = models.FileField(
        'Скан документа об образовании',
        upload_to='education/',
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField('Дата создания', auto_now_add=True)
    updated_at = models.DateTimeField('Дата обновления', auto_now=True)

    class Meta:
        verbose_name = 'Обучаемый'
        verbose_name_plural = 'Обучаемые'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.last_name} {self.first_name} {self.patronymic or ''}".strip()


class _RouteDocumentBase(models.Model):
    """
    Абстрактная базовая модель для документов маршрута.
    """
    student = models.OneToOneField(
        Student,
        on_delete=models.CASCADE,
        related_name='%(class)s',
        verbose_name='Обучаемый',
    )
    is_checked = models.BooleanField('Статус проверки', default=False)
    date_text = models.CharField('Дата', max_length=100, blank=True, null=True)
    operator = models.CharField('Оператор', max_length=255, blank=True, null=True)

    class Meta:
        abstract = True
        ordering = ['-student__created_at']

    def __str__(self):
        return f"{self._meta.verbose_name} — {self.student}"


class CourseEnrollment(_RouteDocumentBase):
    """Поступление на курс."""
    class Meta(_RouteDocumentBase.Meta):
        verbose_name = 'Поступление на курс'
        verbose_name_plural = 'Поступления на курс'


class PersonalDataConsent(_RouteDocumentBase):
    """Согласие на обработку персональных данных."""
    class Meta(_RouteDocumentBase.Meta):
        verbose_name = 'Согласие на обработку ПДн'
        verbose_name_plural = 'Согласия на обработку ПДн'


class EnrollmentApplication(_RouteDocumentBase):
    """Заявление на зачисление."""
    class Meta(_RouteDocumentBase.Meta):
        verbose_name = 'Заявление на зачисление'
        verbose_name_plural = 'Заявления на зачисление'


class EducationContract(_RouteDocumentBase):
    """Договор на обучение."""
    class Meta(_RouteDocumentBase.Meta):
        verbose_name = 'Договор на обучение'
        verbose_name_plural = 'Договоры на обучение'


class PrimaryDocuments(_RouteDocumentBase):
    """Первичные документы."""
    class Meta(_RouteDocumentBase.Meta):
        verbose_name = 'Первичные документы'
        verbose_name_plural = 'Первичные документы'
