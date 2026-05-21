from django.core.management.base import BaseCommand
from parser.scraper import run_scraper
from parser.services import save_student_to_db


class Command(BaseCommand):
    help = "Скрапит всех слушателей со страницы /students и сохраняет в БД"

    def add_arguments(self, parser):
        parser.add_argument(
            "--config",
            type=str,
            default="scraper_config.yaml",
            help="Путь к YAML-конфигу скрапера",
        )

    def handle(self, *args, **options):
        config_path = options["config"]
        self.stdout.write(f"[scraper] Config: {config_path}")

        created = 0
        updated = 0
        errors = 0
        total = 0

        def on_student(data):
            nonlocal created, updated, errors
            try:
                student, route, was_created = save_student_to_db(data)
                if was_created:
                    created += 1
                else:
                    updated += 1
            except Exception as e:
                errors += 1
                self.stderr.write(
                    f"  ERROR: {data.last_name} {data.first_name}: {e}"
                )

        students = run_scraper(config_path=config_path, on_student=on_student)
        total = len(students)

        self.stdout.write(self.style.SUCCESS(
            f"Done! Total: {total}, Created: {created}, "
            f"Updated: {updated}, Errors: {errors}"
        ))
