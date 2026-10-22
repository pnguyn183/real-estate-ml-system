$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $project "venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Không tìm thấy môi trường venv tại: $python. Hãy tạo venv trước bằng: python -m venv venv"
}

$port = 8505
while ($true) {
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
        $listener.Start()
        $listener.Stop()
        break
    }
    catch {
        $port++
    }
}

Write-Host "Khởi động HomeNest bằng venv trên cổng $port..."
& $python (Join-Path $project "run_local.py") --server.headless true --server.port $port
