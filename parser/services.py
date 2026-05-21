"""
Пайплайн сохранения данных скрапера в Django-модели Student и EnrollmentRoute.

Использует update_or_create для идемпотентности: повторный запуск обновляет
существующие записи, а не создаёт дубликаты.
"""

from datetime import datetime
from typing import Optional

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.timezone import make_aware, now

from .models import Student, EnrollmentRoute
from .scraper import StudentData


def _ensure_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return make_aware(dt)
    return dt


def save_student_to_db(data: StudentData) -> tuple:
    """
    Сохраняет одного студента и его маршрут в БД.

    Lookup key: (last_name, first_name).
    Использует update_or_create для Student, затем update_or_create для EnrollmentRoute.
    Сохраняет PDF-файлы документов через ContentFile.

    Returns:
        (student, route, created) — кортеж созданных/обновлённых объектов и флаг создания.
    """
    if not data.first_name or not data.last_name:
        raise ValueError(
            f"Cannot save student without name: last_name={data.last_name!r}, first_name={data.first_name!r}"
        )

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


def save_students_batch(students: list[StudentData]) -> dict:
    """
    Пакетное сохранение списка студентов.

    Returns:
        dict с ключами: created, updated, errors.
    """
    created = 0
    updated = 0
    errors = []

    for data in students:
        try:
            student, route, was_created = save_student_to_db(data)
            if was_created:
                created += 1
            else:
                updated += 1
        except Exception as e:
            name = f"{data.last_name} {data.first_name}"
            errors.append({"name": name, "error": str(e)})

    return {"created": created, "updated": updated, "errors": errors}
