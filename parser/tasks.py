from celery import shared_task


@shared_task
def ping():
    return "ok"


@shared_task
def run_scraper_task(config_path: str = "scraper_config.yaml"):
    """
    Celery-задача: запускает полный скрапер и сохраняет всех студентов в БД.
    """
    from .scraper import run_scraper
    from .services import save_student_to_db

    results = {"created": 0, "updated": 0, "errors": []}

    def on_student(student_data):
        try:
            student, route, was_created = save_student_to_db(student_data)
            if was_created:
                results["created"] += 1
            else:
                results["updated"] += 1
        except Exception as e:
            results["errors"].append({
                "name": f"{student_data.last_name} {student_data.first_name}",
                "error": str(e),
            })

    students = run_scraper(config_path=config_path, on_student=on_student)
    results["total"] = len(students)
    return results


@shared_task
def run_scraper_single_task(config_path: str = "scraper_config.yaml", student_id: str = None):
    """
    Celery-задача: скрапит одного студента по ID.
    """
    from .scraper import run_scraper_single
    from .services import save_student_to_db

    student_data = run_scraper_single(config_path=config_path, student_id=student_id)
    if student_data is None:
        return {"status": "no_data"}

    student, route, was_created = save_student_to_db(student_data)
    return {
        "status": "success",
        "created": was_created,
        "student_id": student.pk,
        "route_id": route.pk,
    }
