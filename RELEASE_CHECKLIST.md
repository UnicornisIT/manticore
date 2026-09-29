# Manticore release checklist

Публикация запрещена, пока каждый обязательный пункт не отмечен и результат не приложен к release notes. Все fault-injection проверки выполняются только на тестовых данных.

## Автоматические проверки

- [ ] Полный `python -m unittest discover -s tests -v`
- [ ] `python -m compileall -q app.py email_validation.py db_safety.py desktop tests`
- [ ] `node --check static/js/desktop-source.js`
- [ ] `node --check desktop/ui/source_dialog.js`
- [ ] `git diff --check`
- [ ] Provider registry validation
- [ ] Фактический Excel создан и повторно открыт

## Установка и миграция

- [ ] Clean install
- [ ] Upgrade с предыдущего release
- [ ] Миграция копии старой SQLite
- [ ] Создание новой пустой SQLite
- [ ] Pre-migration backup проверен и восстановлен
- [ ] Downgrade блокирует более новую schema
- [ ] Симуляция disk full безопасно прекращает операцию

## Windows Desktop

- [ ] Запущен реальный собранный `Manticore.exe`
- [ ] WebView2: запуск, закрытие и повторный запуск
- [ ] Диалог недоступного primary
- [ ] Remember toggle
- [ ] Focus и visibility recovery check
- [ ] Светлая и тёмная тема
- [ ] Некорректный config и восстановление `.bak`
- [ ] Проверено поведение без WebView2 Runtime

## Источники и восстановление

- [ ] Тестовая UNC-шара доступна
- [ ] UNC disconnect / разрыв SMB
- [ ] UNC restore
- [ ] SQLite write lock
- [ ] Повреждённая копия SQLite
- [ ] Forensic copy сохранена
- [ ] Fallback становится dirty только после записи
- [ ] Dirty fallback архивируется перед уходом
- [ ] Primary restore не переключает источник автоматически
- [ ] Просмотр и экспорт локальных изменений
- [ ] Backup restore

## Прикладные сценарии

- [ ] Login, students, applicants и migration
- [ ] Import и export
- [ ] Excel export: строки, заголовки, кириллица и отсутствие служебных полей
- [ ] Email syntax/provider/DNS advisory validation
- [ ] Проверка 10 000 email с повторяющимися доменами

## Решение

- [ ] Все обязательные пункты имеют PASS
- [ ] Непроверенные пункты явно перечислены как `NOT TESTED`
- [ ] Release получает статус `PRODUCTION READY`
