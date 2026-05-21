from django.contrib import admin
from .models import Student, EnrollmentRoute


class EnrollmentRouteInline(admin.StackedInline):
    """
    Позволяет редактировать и видеть маршрут учета 
    прямо внутри карточки обучаемого.
    """
    model = EnrollmentRoute
    can_delete = False
    verbose_name = "Маршрут учета (Документы и Статусы)"
    verbose_name_plural = "Маршрут учета (Документы и Статусы)"
    fieldsets = (
        ('Первичные документы', {
            'fields': ('has_enrollment_application', 'has_personal_data_consent')
        }),
        ('Статус и проверка', {
            'fields': ('status', 'uploaded_at', 'operator', 'verified_at')
        }),
    )


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    """
    Настройка отображения обучаемых в админ-панели.
    """
    # Колоки в общем списке студентов
    list_display = (
        'get_full_name', 
        'get_status', 
        'has_passport_file', 
        'has_name_change_file', 
        'has_education_file', 
        'get_uploaded_at'
    )
    
    # Фильтры в правой панели для быстрого поиска
    list_filter = (
        'route__status', 
        'passport_file', 
        'education_file', 
        'route__uploaded_at'
    )
    
    # Поля, по которым работает поиск
    search_fields = ('last_name', 'first_name', 'patronymic', 'route__operator')
    
    # Инлайны (подключение маршрута на страницу студента)
    inlines = [EnrollmentRouteInline]

    # Кастомные методы для вывода данных из связанной модели Route в список Студентов
    @admin.display(description='ФИО Обучаемого')
    def get_full_name(self, obj):
        return f"{obj.last_name} {obj.first_name} {obj.patronymic or ''}".strip()

    @admin.display(description='Статус', ordering='route__status')
    def get_status(self, obj):
        if hasattr(obj, 'route'):
            return obj.route.get_status_display()
        return 'Нет маршрута'

    @admin.display(description='Дата подгрузки', ordering='route__uploaded_at')
    def get_uploaded_at(self, obj):
        if hasattr(obj, 'route'):
            return obj.route.uploaded_at.strftime('%d.%m.%Y %H:%M')
        return '-'

    @admin.display(description='Паспорт', boolean=True)
    def has_passport_file(self, obj):
        return bool(obj.passport_file)

    @admin.display(description='Смена ФИО', boolean=True)
    def has_name_change_file(self, obj):
        return bool(obj.name_change_file)

    @admin.display(description='Образование', boolean=True)
    def has_education_file(self, obj):
        return bool(obj.education_file)


@admin.register(EnrollmentRoute)
class EnrollmentRouteAdmin(admin.ModelAdmin):
    """
    Отдельный интерфейс для маршрутов (если нужно посмотреть только статусы).
    """
    list_display = ('student', 'status', 'uploaded_at', 'operator', 'verified_at')
    list_filter = ('status', 'uploaded_at', 'verified_at')
    search_fields = ('student__last_name', 'operator')
    readonly_fields = ('uploaded_at',)  # Запрещаем случайное изменение даты загрузки вручную
