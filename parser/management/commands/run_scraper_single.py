import traceback
from django.core.management.base import BaseCommand
from parser.scraper import run_scraper_single
from parser.services import save_student_to_db


class Command(BaseCommand):
    help = "Скрапит одного слушателя по ID и сохраняет в БД"

    def add_arguments(self, parser):
        parser.add_argument(
            "--config",
            type=str,
            default="scraper_config.yaml",
            help="Путь к YAML-конфигу скрапера",
        )
        parser.add_argument(
            "--student-id",
            type=str,
            default=None,
            help="ID студента (как в footer диалога). Если не указан — берёт первую строку.",
        )

    def handle(self, *args, **options):
        config_path = options["config"]
        student_id = options["student_id"]

        self.stdout.write(f"[scraper] Config: {config_path}, Student ID: {student_id or 'first'}")

        try:
            data = run_scraper_single(config_path=config_path, student_id=student_id)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"[scraper] Critical scraper failure: {e}"))
            traceback.print_exc()
            return

        if data is None:
            self.stdout.write(self.style.WARNING("No data received."))
            return

        try:
            student, was_created = save_student_to_db(data)
            action = "Created" if was_created else "Updated"
            self.stdout.write(self.style.SUCCESS(
                f"{action}: {student}"
            ))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error: {e}"))
            traceback.print_exc()
