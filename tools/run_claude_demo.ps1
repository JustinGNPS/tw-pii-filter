[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,

    [switch]$SkipNer,

    # 供自動化驗證使用：只檢查環境，不啟動服務或 Claude Code。
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$EnvFile = Join-Path $RepoRoot ".env"
$LocalBaseUrl = "http://127.0.0.1:$Port"
$ProxyProcess = $null
$StartedProxy = $false
$KeepProxy = $false

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}
function Get-DotEnvValue([string]$Name) {
    $Line = Get-Content -LiteralPath $EnvFile -Encoding UTF8 |
        Where-Object { $_ -match ("^\s*" + [regex]::Escape($Name) + "\s*=") } |
        Select-Object -Last 1
    if ($null -eq $Line) {
        return ""
    }
    return (($Line -split "=", 2)[1].Trim().Trim('"').Trim("'"))
}

function Get-ProxyHealth {
    try {
        return Invoke-RestMethod -Uri "$LocalBaseUrl/healthz" -TimeoutSec 2
    }
    catch {
        return $null
    }
}

function Stop-OwnedProxy {
    if ($StartedProxy -and $null -ne $ProxyProcess -and -not $ProxyProcess.HasExited) {
        Stop-Process -Id $ProxyProcess.Id -Force -ErrorAction SilentlyContinue
    }
}

try {
    Set-Location -LiteralPath $RepoRoot

    Write-Step "檢查本機環境"
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        throw "找不到 .venv。請先雙擊「啟動.bat」完成第一次安裝。"
    }
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        throw "找不到 .env。請先雙擊「啟動.bat」讓系統建立設定檔。"
    }

    $UpstreamBaseUrl = Get-DotEnvValue "UPSTREAM_BASE_URL"
    $UpstreamApiKey = Get-DotEnvValue "UPSTREAM_API_KEY"
    $DefaultModel = Get-DotEnvValue "DEFAULT_MODEL"
    if ([string]::IsNullOrWhiteSpace($UpstreamBaseUrl)) {
        throw ".env 的 UPSTREAM_BASE_URL 尚未設定。"
    }
    if ([string]::IsNullOrWhiteSpace($UpstreamApiKey)) {
        throw ".env 的 UPSTREAM_API_KEY 尚未設定。"
    }
    if ([string]::IsNullOrWhiteSpace($DefaultModel)) {
        throw ".env 的 DEFAULT_MODEL 尚未設定。"
    }

    $ClaudeCommand = Get-Command "claude" -ErrorAction SilentlyContinue
    if ($null -eq $ClaudeCommand) {
        throw "找不到 Claude Code。請先安裝並確認在 PowerShell 輸入 claude --version 有結果。"
    }

    if (-not $SkipNer) {
        & $PythonExe -c "import torch, transformers" 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw "語意層套件尚未安裝。請先執行「啟動.bat」選 1，再對安裝語意層回答 y。"
        }
    }
    & $PythonExe -c "import rapidocr, onnxruntime, pypdfium2, PIL, cv2" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "本機 OCR 套件尚未安裝。請先執行「啟動.bat」選 1，讓啟動器自動補裝。"
    }

    Write-Host "  [OK] 上游：$UpstreamBaseUrl"
    Write-Host "  [OK] 模型：$DefaultModel"
    Write-Host "  [OK] API Key：已載入（內容不顯示）"
    Write-Host "  [OK] Claude Code：$($ClaudeCommand.Source)"
    Write-Host "  [OK] 語意層：$(if ($SkipNer) { '本次略過' } else { '已安裝，將開啟' })"
    Write-Host "  [OK] 附件 OCR：已安裝（圖片與掃描 PDF 均在本機處理）"

    if ($PreflightOnly) {
        Write-Host ""
        Write-Host "預檢完成；未啟動 Proxy 或 Claude Code。" -ForegroundColor Green
        exit 0
    }

    Write-Step "啟動或沿用本機 Proxy"
    $Health = Get-ProxyHealth
    if ($null -eq $Health) {
        # 只影響這次新啟動的 Proxy，不修改使用者的 .env。
        $env:PII_ENABLE_DEMO = "1"
        $env:PII_ENABLE_NER = $(if ($SkipNer) { "0" } else { "1" })
        $env:PROXY_CONNECT_TIMEOUT = "30"
        $env:PYTHONIOENCODING = "utf-8"

        # 使用者需要在比賽現場看得到遮蔽與還原紀錄，因此刻意開可見視窗。
        $ProxyProcess = Start-Process `
            -FilePath $PythonExe `
            -ArgumentList @("-m", "uvicorn", "proxy.main:app", "--host", "127.0.0.1", "--port", "$Port") `
            -WorkingDirectory $RepoRoot `
            -PassThru
        $StartedProxy = $true

        $Deadline = [DateTime]::UtcNow.AddMinutes(5)
        do {
            Start-Sleep -Milliseconds 500
            if ($ProxyProcess.HasExited) {
                throw "Proxy 啟動後立刻結束。請查看剛才開啟的 Proxy 視窗。"
            }
            $Health = Get-ProxyHealth
        } while ($null -eq $Health -and [DateTime]::UtcNow -lt $Deadline)

        if ($null -eq $Health) {
            throw "等待 5 分鐘後 Proxy 仍未就緒；請查看 Proxy 視窗中的錯誤。"
        }
    }
    else {
        Write-Host "  [提示] 埠 $Port 已有 tw-pii-filter Proxy，直接沿用。"
    }

    if (-not $Health.upstream_key_loaded) {
        throw "Proxy 已啟動，但沒有載入上游 API Key。請檢查 .env 後重新執行。"
    }
    if (-not $SkipNer -and -not $Health.ner_enabled) {
        throw "目前埠 $Port 的 Proxy 沒有開啟語意層。請先停止舊 Proxy，再重新執行本腳本。"
    }
    if (-not $Health.ocr_available) {
        throw "目前 Proxy 無法載入本機 OCR。請停止舊 Proxy，執行「啟動.bat」選 1 補裝後重試。"
    }
    if ($Health.image_policy -ne "ocr" -or $Health.document_policy -ne "extract") {
        throw "目前附件策略未開啟 OCR。請到 /demo 的「設定」把圖片設為本機 OCR、PDF 設為抽字／OCR，再重試。"
    }

    # Claude Code 啟動時會先探測此端點。必須由本機立即回 200，不能送到上游。
    try {
        $Probe = Invoke-WebRequest -Uri "$LocalBaseUrl/api/hello" -Method Head -UseBasicParsing -TimeoutSec 5
    }
    catch {
        throw "Claude Code 探測端點沒有在 5 秒內回應。請確認 Proxy 已重新啟動並包含最新修正。"
    }
    if ($Probe.StatusCode -ne 200) {
        throw "Claude Code 探測端點回傳 $($Probe.StatusCode)，預期為 200。"
    }

    Write-Host "  [OK] Proxy：$LocalBaseUrl"
    Write-Host "  [OK] 語意層：$(if ($Health.ner_enabled) { '開啟' } else { '關閉' })"
    Write-Host "  [OK] 附件 OCR：圖片=$($Health.image_policy)，PDF=$($Health.document_policy)"
    Write-Host "  [OK] Claude Code 探測：200"

    $DemoPrompt = @"
請將下列虛構資料整理成 Markdown 表格，表格中必須保留每一項資料：

陳冠宇的身分證字號是 A123456789，目前擔任新創科技公司的資料工程師，
電話是 0912-345-678，電子郵件是 kuanyu.test@example.com，
住在臺北市中正區重慶南路一段122號。
"@
    try {
        Set-Clipboard -Value $DemoPrompt
        Write-Host "  [OK] 虛構測試提示已複製到剪貼簿"
    }
    catch {
        Write-Warning "無法存取剪貼簿；Claude Code 開啟後請自行輸入測試內容。"
    }

    Write-Step "開啟真正的 Claude Code"
    # 真正金鑰只存在 Proxy 的 .env；Claude Code 僅能看到本機位址與 dummy。
    $env:ANTHROPIC_BASE_URL = $LocalBaseUrl
    $env:ANTHROPIC_API_KEY = "dummy"
    Remove-Item Env:ANTHROPIC_AUTH_TOKEN -ErrorAction SilentlyContinue

    Write-Host "Claude Code 開啟後按 Ctrl+V 貼上虛構測試提示。"
    Write-Host "同時查看 Proxy 視窗是否顯示「已遮蔽」與「還原」紀錄。"
    Write-Host ""
    & $ClaudeCommand.Source --bare

    if ($StartedProxy -and $null -ne $ProxyProcess -and -not $ProxyProcess.HasExited) {
        Write-Host ""
        $Choice = Read-Host "Claude Code 已結束。按 Enter 停止本次 Proxy；輸入 K 後 Enter 可保留"
        if ($Choice.Trim().ToLowerInvariant() -eq "k") {
            $KeepProxy = $true
            Write-Host "Proxy 保持執行；稍後可在 Proxy 視窗按 Ctrl+C 停止。"
        }
    }
}
catch {
    Write-Host ""
    Write-Host "[錯誤] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    if (-not $KeepProxy) {
        Stop-OwnedProxy
    }
}
