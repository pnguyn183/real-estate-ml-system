@echo off
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
    echo Tim cổng trống...
    for /L %%P in (8505,1,8510) do (
        powershell -NoProfile -Command "$socket = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, %%P); try { $socket.Start(); Write-Output %%P; $socket.Stop() } catch { } finally { $socket.Dispose() }" > temp_port.txt 2>nul
        if not errorlevel 1 (
            for /f %%I in (temp_port.txt) do (
                echo Khởi động HomeNest trên cổng %%I...
                venv\Scripts\python.exe run_local.py --server.headless true --server.port %%I
                goto end
            )
        )
    )
    :end
    del temp_port.txt 2>nul
) else (
    echo Khong tim thay venv. Hay chay: python -m venv venv
    pause
)
