@echo off
chcp 65001 > nul
echo ===================================================
echo   Создание владельца (Owner) для Grade Book Server
echo ===================================================
echo.

set /p username="Введите username (по умолчанию 'owner'): "
if "%username%"=="" set username=owner

:ask_pass
set /p password="Введите пароль: "
if "%password%"=="" (
    echo Пароль не может быть пустым!
    goto ask_pass
)

echo.
echo Создаем пользователя '%username%'...
python scripts/create_owner.py --username "%username%" --password "%password%"

echo.
if %errorlevel% equ 0 (
    echo [УСПЕХ] Владелец создан или уже существует.
) else (
    echo [ОШИБКА] Не удалось создать владельца. Проверьте вывод выше.
)

echo.
pause
