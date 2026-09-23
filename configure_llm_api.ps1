$ErrorActionPreference = "Stop"

$baseUrl = "https://llmapi.tongji.edu.cn/v1"
$model = "DeepSeek-V4-Flash"

Write-Host "[AI Assist] Configure OpenAI-compatible LLM API"
Write-Host "[AI Assist] Base URL: $baseUrl"
Write-Host "[AI Assist] Model: $model"
Write-Host ""

$secureKey = Read-Host "Paste your API key (input hidden)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $apiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim()
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

if ([string]::IsNullOrWhiteSpace($apiKey)) {
    Write-Error "API key is empty. Nothing was changed."
    exit 1
}

$values = @{
    "DEEPSEEK_API_KEY" = $apiKey
    "DEEPSEEK_API_URL" = $baseUrl
    "DEEPSEEK_API_BASE_URL" = $baseUrl
    "DEEPSEEK_MODEL" = $model
    "LLM_API_KEY" = $apiKey
    "LLM_BASE_URL" = $baseUrl
    "LLM_MODEL" = $model
}

foreach ($item in $values.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable($item.Key, $item.Value, "User")
    Set-Item -Path "Env:$($item.Key)" -Value $item.Value
}

Write-Host ""
Write-Host "[AI Assist] Saved LLM environment variables for the current Windows user."
Write-Host "[AI Assist] API key was saved but not printed."

$backendPython = Join-Path $PSScriptRoot "backend\.venv\Scripts\python.exe"
if (Test-Path $backendPython) {
    Write-Host "[AI Assist] Testing backend LLM configuration..."
    Push-Location (Join-Path $PSScriptRoot "backend")
    try {
        & $backendPython -c "from app.services.llm_client import get_llm_status, ping_llm; import json; print(json.dumps(get_llm_status(), ensure_ascii=False)); print(json.dumps(ping_llm('请用 JSON 返回连通性测试结果', timeout=30), ensure_ascii=False))"
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Host "[AI Assist] backend\.venv was not found; skipping API ping."
}

Write-Host ""
Write-Host "[AI Assist] Done. Restart the backend terminal so it can read the new user environment."
