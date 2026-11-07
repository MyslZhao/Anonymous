# 生成 Linux 服务器用的离线依赖包（wheelhouse）。
# 用法：uv run ... 由 uv 统一提供 python/pip，避免依赖系统 Python（服务器上是 3.6 也没关系）
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\make-wheelhouse.ps1 [-Out wheelhouse]
param(
    [string]$Out = "wheelhouse",
    [string]$Platform = "manylinux_2_28_x86_64"
)

$ErrorActionPreference = "Stop"

$reqFile = Join-Path $env:TEMP "campusmind-requirements.txt"
Write-Host "==> 从 uv.lock 导出运行时依赖"
uv export --frozen --no-dev --no-hashes --format requirements-txt -o $reqFile
if ($LASTEXITCODE -ne 0) { throw "uv export 失败" }

# uv export 会带上环境标记（例如 uvloop 的 sys_platform != 'win32'）。
# 在 Windows 上打包时必须去掉标记，否则 uvloop 这类 Linux 专属依赖会被跳过，
# 到服务器上反而缺失。这里把 "pkg==ver ; marker" 统一化简为 "pkg==ver"。
$lines = Get-Content $reqFile | ForEach-Object {
    if ($_ -match '^(?<pkg>[^\s#][^;]*?)\s*;\s*.+$') { $Matches['pkg'].Trim() } else { $_ }
}
Set-Content -Path $reqFile -Value $lines -Encoding UTF8

Write-Host "==> 下载 $Platform / CPython 3.12 的二进制 wheel 到 $Out"
New-Item -ItemType Directory -Force -Path $Out | Out-Null
uv run --no-project --with pip -- python -m pip download `
    --only-binary=:all: `
    --platform $Platform `
    --platform manylinux2014_x86_64 `
    --platform linux_x86_64 `
    --python-version 3.12 `
    --implementation cp `
    --abi cp312 `
    --dest $Out `
    -r $reqFile
if ($LASTEXITCODE -ne 0) { throw "pip download 失败" }

Copy-Item $reqFile (Join-Path $Out "requirements.txt") -Force
Remove-Item $reqFile
$count = (Get-ChildItem $Out -Filter *.whl | Measure-Object).Count
Write-Host "==> 完成：$count 个 wheel，已写入 $Out\requirements.txt"
