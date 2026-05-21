"""
Пайплайн сохранения данных скрапера в Django-модели.

Сохраняет Student + 5 моделей документов маршрута через update_or_create.
"""

from typing import Optional

from django.core.files.base import ContentFile
from django.db import transaction

from .models import (
    Student,
    CourseEnrollment,
    PersonalDataConsent,
    EnrollmentApplication,
    EducationContract,
    PrimaryDocuments,
)
from .scraper import StudentData, ROUTE_MODEL_MAPPING


def save_student_to_db(data: StudentData) -> tuple:
    """
    Сохраняет студента и все документы маршрута в БД.

    Returns:
        (student, created) — объект студента и флаг создания.
    """
    if not data.first_name or not data.last_name:
        raise ValueError(
            f"Cannot save student without name: last_name={data.last_name!r}, first_name={data.first_name!r}"
        )

    MODEL_CLASS_MAP = {
        "CourseEnrollment": CourseEnrollment,
        "PersonalDataConsent": PersonalDataConsent,
        "EnrollmentApplication": EnrollmentApplication,
        "EducationContract": EducationContract,
        "PrimaryDocuments": PrimaryDocuments,
    }

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
                continue

            model_cls = MODEL_CLASS_MAP.get(model_name)
            if not model_cls:
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
