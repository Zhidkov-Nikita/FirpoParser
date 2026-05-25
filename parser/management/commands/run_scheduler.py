import logging
import sys

from django.core.management.base import BaseCommand
from django_apscheduler.jobstores import DjangoJobStore, register_events
from apscheduler.schedulers.blocking import BlockingScheduler

from parser.scraper import run_scraper
from parser.services import save_student_to_db

logger = logging.getLogger(__name__)


def run_parser_every_hour_job(config_path: str = "scraper_config.yaml"):
    """
    Глобальная функция задачи для APScheduler.
    APScheduler сериализует её по полному Python-пути:
    parser.management.commands.run_scheduler.run_parser_every_hour_job
    """
    logger.info("Внутренний триггер планировщика: запуск ежечасного скрапинга...")

    created = 0
    updated = 0
    errors = 0

    def on_student(data):
        nonlocal created, updated, errors
        try:
            _, was_created = save_student_to_db(data)
            if was_created:
                created += 1
            else:
                updated += 1
        except Exception as e:
            errors += 1
            logger.error(f"  ERROR: {data.last_name} {data.first_name}: {e}")

    try:
        students = run_scraper(
            config_path=config_path,
            test_mode=False,
            on_student=on_student,
        )
        total = len(students)
        logger.info(
            f"Ежечасный скрапинг успешно завершен. "
            f"Всего: {total}, Создано: {created}, "
            f"Обновлено: {updated}, Ошибок: {errors}"
        )
    except Exception as e:
        logger.error(f"Ошибка в процессе выполнения ежечасного скрапинга: {e}")


class Command(BaseCommand):
    help = "Запуск встроенного планировщика задач Django Jobs"

    def add_arguments(self, parser):
        parser.add_argument(
            "--config",
            type=str,
            default="scraper_config.yaml",
            help="Путь к YAML-конфигу скрапера",
        )
        parser.add_argument(
            "--test",
            action="store_true",
            default=False,
            help="Тестовый режим: запустить задачу один раз прямо сейчас "
                 "(без установки интервального расписания)",
        )

    def handle(self, *args, **options):
        config_path = options["config"]
        test_mode = options["test"]

        if test_mode:
            self.stdout.write(self.style.WARNING(
                "[scheduler] TEST MODE — запуск одной задачи прямо сейчас"
            ))
            run_parser_every_hour_job(config_path=config_path)
            self.stdout.write(self.style.SUCCESS("[scheduler] Тест завершён"))
            return

        scheduler = BlockingScheduler()
        scheduler.add_jobstore(DjangoJobStore(), "default")

        scheduler.add_job(
            run_parser_every_hour_job,
            trigger="interval",
            hours=1,
            id="run_parser_every_hour",
            max_instances=1,
            replace_existing=True,
            kwargs={"config_path": config_path},
        )

        register_events(scheduler)
        self.stdout.write(self.style.SUCCESS(
            "[scheduler] Воркер успешно запущен и готов к работе!"
        ))

        try:
            scheduler.start()
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING(
                "[scheduler] Воркер остановлен пользователем."
            ))
            scheduler.shutdown()
            sys.exit(0)
