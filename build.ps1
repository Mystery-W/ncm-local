<#
.SYNOPSIS
在独立项目打包环境中生成 Windows x64 可执行文件。
.DESCRIPTION
只读复用所选 Python 环境；不向 base 或现有 Conda 环境安装任何包。
需要网络获取独立打包环境缺失的依赖，失败时停止并保留诊断输出。
#>
param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$buildEnvironment = Join-Path $projectRoot '.build-env'
& $Python -m venv --system-site-packages $buildEnvironment
if ($LASTEXITCODE -ne 0) { throw '创建独立打包环境失败' }
$buildPython = Join-Path $buildEnvironment 'Scripts\python.exe'
& $buildPython -m pip install -r (Join-Path $projectRoot 'requirements.txt') 'pyinstaller==6.16.0'
if ($LASTEXITCODE -ne 0) { throw '安装打包依赖失败' }
& $buildPython -m PyInstaller --noconfirm --clean --onefile --windowed --name NCM-Local `
    --distpath (Join-Path $projectRoot 'dist') --workpath (Join-Path $projectRoot 'build') `
    --specpath (Join-Path $projectRoot 'build') --collect-all mutagen `
    --copy-metadata cryptography --copy-metadata mutagen (Join-Path $projectRoot 'app.py')
if ($LASTEXITCODE -ne 0) { throw '打包失败，请检查输出日志' }
Write-Host "打包完成：$(Join-Path $projectRoot 'dist\NCM-Local.exe')"
Write-Host '分发时请附上对应源码、LICENSE、THIRD_PARTY.md 和 licenses 目录。'
