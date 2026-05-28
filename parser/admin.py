from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Student,
    CourseEnrollment,
    PersonalDataConsent,
    EnrollmentApplication,
    EducationContract,
    PrimaryDocuments,
)


class RouteDocumentInlineBase(admin.StackedInline):
    """Базовый класс для инлайнов документов маршрута."""
    extra = 0
    can_delete = False
    fields = ('is_checked', 'date_text', 'operator')
    readonly_fields = ('date_text', 'operator')


class CourseEnrollmentInline(RouteDocumentInlineBase):
    model = CourseEnrollment
    verbose_name = "Поступление на курс"
    verbose_name_plural = "Поступление на курс"


class PersonalDataConsentInline(RouteDocumentInlineBase):
    model = PersonalDataConsent
    verbose_name = "Согласие на обработку ПДн"
    verbose_name_plural = "Согласие на обработку ПДн"


class EnrollmentApplicationInline(RouteDocumentInlineBase):
    model = EnrollmentApplication
    verbose_name = "Заявление на зачисление"
    verbose_name_plural = "Заявление на зачисление"


class EducationContractInline(RouteDocumentInlineBase):
    model = EducationContract
    verbose_name = "Договор на обучение"
    verbose_name_plural = "Договор на обучение"


class PrimaryDocumentsInline(RouteDocumentInlineBase):
    model = PrimaryDocuments
    verbose_name = "Первичные документы"
    verbose_name_plural = "Первичные документы"


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        'student_id',
        'get_full_name',
        'email',
        'course',
        'colored_status',
        'has_passport_file',
        'has_education_file',
    )
    list_filter = (
        'status',
    )
    search_fields = (
        'student_id',
        'last_name',
        'first_name',
        'patronymic',
        'email',
    )
    inlines = [
        CourseEnrollmentInline,
        PersonalDataConsentInline,
        EnrollmentApplicationInline,
        EducationContractInline,
        PrimaryDocumentsInline,
    ]
    readonly_fields = ('created_at', 'updated_at')

    @admin.display(description='ФИО')
    def get_full_name(self, obj):
        return f"{obj.last_name} {obj.first_name} {obj.patronymic or ''}".strip()

    @admin.display(description='Статус')
    def colored_status(self, obj):
        if not obj.status:
            return ""
        colors = {
            "Услуга прекращена": "red",
            "Прошёл ИА": "green",
            "Обучается": "green",
            "Заключен 3-х сторонний": "purple",
            "Заключен договор": "purple",
            "Подписан СЭП": "orange",
            "Заявка одобрена": "orange",
            "Новая заявка": "gray",
        }
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color, obj.status,
        )

    @admin.display(description='Паспорт', boolean=True)
    def has_passport_file(self, obj):
        return bool(obj.passport_file)

    @admin.display(description='Образование', boolean=True)
    def has_education_file(self, obj):
        return bool(obj.education_file)


@admin.register(CourseEnrollment)
class CourseEnrollmentAdmin(admin.ModelAdmin):
    list_display = ('student', 'is_checked', 'date_text', 'operator')
    list_filter = ('is_checked',)
    search_fields = ('student__last_name', 'student__first_name', 'operator')


@admin.register(PersonalDataConsent)
class PersonalDataConsentAdmin(admin.ModelAdmin):
    list_display = ('student', 'is_checked', 'date_text', 'operator')
    list_filter = ('is_checked',)
    search_fields = ('student__last_name', 'student__first_name', 'operator')


@admin.register(EnrollmentApplication)
class EnrollmentApplicationAdmin(admin.ModelAdmin):
    list_display = ('student', 'is_checked', 'date_text', 'operator')
    list_filter = ('is_checked',)
    search_fields = ('student__last_name', 'student__first_name', 'operator')


@admin.register(EducationContract)
class EducationContractAdmin(admin.ModelAdmin):
    list_display = ('student', 'is_checked', 'date_text', 'operator')
    list_filter = ('is_checked',)
    search_fields = ('student__last_name', 'student__first_name', 'operator')


@admin.register(PrimaryDocuments)
class PrimaryDocumentsAdmin(admin.ModelAdmin):
    list_display = ('student', 'is_checked', 'date_text', 'operator')
    list_filter = ('is_checked',)
    search_fields = ('student__last_name', 'student__first_name', 'operator')
