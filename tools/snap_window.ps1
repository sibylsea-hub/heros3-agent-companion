# Start a program, wait, capture only its own top-level windows (PrintWindow), then close it.
# Usage: powershell -File snap_window.ps1 -Exe <path> [-ExeArgs "<args>"] -Out <png> [-Wait 10] [-Keep]
# Every visible window of the process is saved; the 2nd, 3rd... get -1, -2 suffixes.
param(
    [Parameter(Mandatory)] [string] $Exe,
    [string] $ExeArgs = "",
    [Parameter(Mandatory)] [string] $Out,
    [int] $Wait = 10,
    [switch] $Keep
)

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class Win {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
    public static List<IntPtr> WindowsOf(uint pid) {
        var found = new List<IntPtr>();
        EnumWindows((h, l) => {
            uint p; GetWindowThreadProcessId(h, out p);
            RECT r; GetWindowRect(h, out r);
            if (p == pid && IsWindowVisible(h) && r.R - r.L > 50 && r.B - r.T > 50) found.Add(h);
            return true;
        }, IntPtr.Zero);
        return found;
    }
    public static string Title(IntPtr h) { var s = new StringBuilder(256); GetWindowText(h, s, 256); return s.ToString(); }
}
"@

$startArgs = @{ FilePath = $Exe; WorkingDirectory = (Split-Path $Exe); PassThru = $true }
if ($ExeArgs) { $startArgs.ArgumentList = $ExeArgs }
$p = Start-Process @startArgs
Start-Sleep -Seconds $Wait
$wins = [Win]::WindowsOf([uint32]$p.Id)
if ($wins.Count -eq 0) { Write-Output "no window"; if (-not $Keep) { Stop-Process -Id $p.Id -Force }; exit 1 }

$i = 0
foreach ($h in $wins) {
    $r = New-Object Win+RECT
    [Win]::GetWindowRect($h, [ref]$r) | Out-Null
    $w = $r.R - $r.L; $hh = $r.B - $r.T
    $bmp = New-Object System.Drawing.Bitmap $w, $hh
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $hdc = $g.GetHdc()
    [Win]::PrintWindow($h, $hdc, 2) | Out-Null   # 2 = PW_RENDERFULLCONTENT (works for GPU-rendered windows)
    $g.ReleaseHdc($hdc); $g.Dispose()
    $file = if ($i -eq 0) { $Out } else { [IO.Path]::ChangeExtension($Out, $null).TrimEnd('.') + "-$i.png" }
    $bmp.Save($file, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
    Write-Output ("saved {0} ({1}x{2}) '{3}'" -f $file, $w, $hh, [Win]::Title($h))
    $i++
}
if (-not $Keep) { Stop-Process -Id $p.Id -Force }
