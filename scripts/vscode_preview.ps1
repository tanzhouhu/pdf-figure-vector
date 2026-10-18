param(
    [Parameter(Mandatory = $true)][string]$Url,
    [string]$Command = 'Open Integrated Browser',
    [string]$Needle = ''
)

# Opens $Url inside VS Code using UI Automation only: no typing the command,
# no clipboard, no text can end up in an editor or chat box.
#
# Flow: expand a menu -> invoke "Command Palette" -> set the palette input
# value -> invoke the browser command -> set the URL field -> Enter.
# If any step cannot be verified, fall back to the default browser.

$ErrorActionPreference = 'SilentlyContinue'

# Optional diagnostic log: set PDFFIG_TRACE to a file path.
$traceFile = $env:PDFFIG_TRACE
function T($m) {
    if ($traceFile) { Add-Content -Path $traceFile -Value ("{0:HH:mm:ss.fff} {1}" -f (Get-Date), $m) }
}
T "params bound"
Add-Type -AssemblyName System.Windows.Forms
T "winforms"
Add-Type -AssemblyName UIAutomationClient
T "uia"
Add-Type -AssemblyName UIAutomationTypes
T "uiatypes"
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint attach, uint attachTo, bool on);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);

    public static bool ForceForeground(IntPtr hWnd) {
        if (IsIconic(hWnd)) ShowWindow(hWnd, 9);
        uint dummy;
        uint fg = GetWindowThreadProcessId(GetForegroundWindow(), out dummy);
        uint me = GetCurrentThreadId();
        bool attached = false;
        if (fg != me) attached = AttachThreadInput(fg, me, true);
        keybd_event(0x12, 0, 0, UIntPtr.Zero);
        keybd_event(0x12, 0, 2, UIntPtr.Zero);
        bool ok = SetForegroundWindow(hWnd);
        if (attached) AttachThreadInput(fg, me, false);
        return ok;
    }
}
"@

# The caller opens the default browser; doing it here would leak the parent's
# stdout/stderr handles into the browser process.
function Exit-Fallback {
    Write-Output 'FALLBACK'
    exit 0
}

function Find-ByType($root, $type) {
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty, $type)
    return $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
}

$proc = Get-Process -Name Code, 'Code - Insiders', VSCodium |
    Where-Object { $_.MainWindowHandle -ne 0 } |
    Select-Object -First 1
if (-not $proc) { Write-Output 'NO_VSCODE'; Exit-Fallback }

T "proc=$($proc.Id)"
[Win32]::ForceForeground($proc.MainWindowHandle) | Out-Null
T "foreground forced"
Start-Sleep -Milliseconds 500
$fgPid = 0
[Win32]::GetWindowThreadProcessId([Win32]::GetForegroundWindow(), [ref]$fgPid) | Out-Null
if ($fgPid -ne $proc.Id) { Write-Output 'NOT_FOREGROUND'; Exit-Fallback }

$root = [System.Windows.Automation.AutomationElement]::FromHandle($proc.MainWindowHandle)
if (-not $root) { Exit-Fallback }
T "uia root ready"

# ---- 1. open the command palette through the View menu -------------------
$paletteCmd = $null
$menus = Find-ByType $root ([System.Windows.Automation.ControlType]::MenuItem)
foreach ($m in $menus) {
    $ec = $null
    if (-not $m.TryGetCurrentPattern(
            [System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$ec)) { continue }
    try { $ec.Expand() } catch { continue }
    Start-Sleep -Milliseconds 250
    $items = Find-ByType $root ([System.Windows.Automation.ControlType]::MenuItem)
    foreach ($it in $items) {
        if ($it.Current.Name -match 'Ctrl\+Shift\+P') { $paletteCmd = $it; break }
    }
    if ($paletteCmd) { break }
    try { $ec.Collapse() } catch { }
}
if (-not $paletteCmd) { Write-Output 'NO_PALETTE'; Exit-Fallback }
T "palette item found"
$ip = $null
if (-not $paletteCmd.TryGetCurrentPattern(
        [System.Windows.Automation.InvokePattern]::Pattern, [ref]$ip)) {
    Exit-Fallback
}
try { $ip.Invoke() } catch { Exit-Fallback }
T "palette invoked"
Start-Sleep -Milliseconds 900

# ---- 2. type the command name into the palette ---------------------------
$listCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'quickInput_list')
$list = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $listCond)
if (-not $list) { Write-Output 'NO_QUICK_INPUT'; Exit-Fallback }
T "quick input list found"

$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$group = $walker.GetParent($list)
$input = $null
if ($group) {
    $edits = Find-ByType $group ([System.Windows.Automation.ControlType]::Edit)
    if ($edits.Count -gt 0) { $input = $edits[0] }
}
if ($input) {
    $vp = $null
    if ($input.TryGetCurrentPattern(
            [System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
        $vp.SetValue($Command)
        T "command typed"
        Start-Sleep -Milliseconds 700
    }
}

# ---- 3. invoke the browser command ---------------------------------------
$target = $null
$items = Find-ByType $list ([System.Windows.Automation.ControlType]::ListItem)
foreach ($it in $items) {
    if ($it.Current.Name -match 'Integrated Browser|集成浏览器|Simple Browser|简单浏览器') {
        $target = $it; break
    }
}
if (-not $target) { Write-Output 'NO_BROWSER_CMD'; Exit-Fallback }

$inv = $null
if (-not $target.TryGetCurrentPattern(
        [System.Windows.Automation.InvokePattern]::Pattern, [ref]$inv)) {
    Exit-Fallback
}
try { $inv.Invoke() } catch { Exit-Fallback }
T "browser command invoked"
Start-Sleep -Milliseconds 2000

# ---- 4. fill the URL field and press Enter -------------------------------
$field = [System.Windows.Automation.AutomationElement]::FocusedElement
if (-not $field -or $field.Current.ProcessId -ne $proc.Id -or
    $field.Current.ControlType.ProgrammaticName -ne 'ControlType.Edit') {
    Write-Output 'NO_URL_FIELD'
    Exit-Fallback
}
$vp2 = $null
if (-not $field.TryGetCurrentPattern(
        [System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp2)) {
    Exit-Fallback
}
$vp2.SetValue($Url)
T "url typed"
Start-Sleep -Milliseconds 400

# Only send Enter when VS Code owns the foreground and the URL box is alive.
# Another window (e.g. a browser opened earlier) can steal focus mid-run, so
# re-grab the foreground and re-check a few times before typing.
$ready = $false
for ($i = 0; $i -lt 4; $i++) {
    $fgPid = 0
    [Win32]::GetWindowThreadProcessId([Win32]::GetForegroundWindow(), [ref]$fgPid) | Out-Null
    if ($fgPid -ne $proc.Id) {
        [Win32]::ForceForeground($proc.MainWindowHandle) | Out-Null
        Start-Sleep -Milliseconds 300
        # Forcing the foreground can move focus out of the URL box; put the
        # value back so Enter still submits the right address.
        try { $vp2.SetValue($Url) } catch { }
        Start-Sleep -Milliseconds 250
        $fgPid = 0
        [Win32]::GetWindowThreadProcessId([Win32]::GetForegroundWindow(), [ref]$fgPid) | Out-Null
    }
    # The quick input list may hold focus instead of the text box, but Enter
    # still submits the box, so require only that the box is alive.
    try { $alive = $field.Current.Name } catch { $alive = $null }
    T ("retry {0}: fg={1} proc={2} boxAlive={3}" -f $i, $fgPid, $proc.Id, [bool]$alive)
    if ($fgPid -eq $proc.Id -and $alive) {
        $ready = $true
        break
    }
}
if (-not $ready) { Exit-Fallback }
T "ready to send enter"
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
T "enter sent"

# ---- 5. verify ----------------------------------------------------------
for ($i = 0; $i -lt 16; $i++) {
    Start-Sleep -Milliseconds 500
    $now = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
    if ($now -and $Needle -and $now.MainWindowTitle -like "*$Needle*") {
        Write-Output 'VSCODE_OK'
        exit 0
    }
}
Write-Output 'VERIFY_FAILED'
Exit-Fallback
