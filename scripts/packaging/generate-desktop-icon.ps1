$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$OutputPath = Join-Path $RepoRoot "desktop\build\icon.ico"
$Sizes = @(16, 24, 32, 48, 64, 128, 256)

Add-Type -AssemblyName System.Drawing.Common

function New-RoundedRectanglePath {
  param(
    [float]$X,
    [float]$Y,
    [float]$Width,
    [float]$Height,
    [float]$Radius
  )

  $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
  $diameter = $Radius * 2

  $path.AddArc($X, $Y, $diameter, $diameter, 180, 90)
  $path.AddArc($X + $Width - $diameter, $Y, $diameter, $diameter, 270, 90)
  $path.AddArc($X + $Width - $diameter, $Y + $Height - $diameter, $diameter, $diameter, 0, 90)
  $path.AddArc($X, $Y + $Height - $diameter, $diameter, $diameter, 90, 90)
  $path.CloseFigure()

  return $path
}

function New-LogoPngBytes {
  param([int]$Size)

  $bitmap = [System.Drawing.Bitmap]::new($Size, $Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  $graphics.Clear([System.Drawing.Color]::Transparent)
  $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality

  $primary = [System.Drawing.ColorTranslator]::FromHtml("#991b1b")
  $white = [System.Drawing.Color]::White

  $backgroundRadius = [Math]::Max(3.0, $Size * 0.1875)
  $backgroundPath = New-RoundedRectanglePath 0 0 $Size $Size $backgroundRadius
  $backgroundBrush = [System.Drawing.SolidBrush]::new($primary)
  $graphics.FillPath($backgroundBrush, $backgroundPath)

  $scale = $Size / 16.0
  $strokeWidth = [Math]::Max(1.0, [Math]::Round($scale, 2))
  $pen = [System.Drawing.Pen]::new($white, $strokeWidth)
  $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
  $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
  $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round

  $mailX = 3.5 * $scale
  $mailY = 4.5 * $scale
  $mailWidth = 9.0 * $scale
  $mailHeight = 7.0 * $scale
  $mailRadius = [Math]::Max(1.0, 1.0 * $scale)
  $mailPath = New-RoundedRectanglePath $mailX $mailY $mailWidth $mailHeight $mailRadius
  $graphics.DrawPath($pen, $mailPath)

  [System.Drawing.PointF[]]$points = @(
    [System.Drawing.PointF]::new(12.5 * $scale, 5.5 * $scale),
    [System.Drawing.PointF]::new(8.9 * $scale, 8.1 * $scale),
    [System.Drawing.PointF]::new(8.0 * $scale, 8.4 * $scale),
    [System.Drawing.PointF]::new(7.1 * $scale, 8.1 * $scale),
    [System.Drawing.PointF]::new(3.5 * $scale, 5.5 * $scale)
  )
  $graphics.DrawLines($pen, $points)

  $stream = [System.IO.MemoryStream]::new()
  $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
  $bytes = $stream.ToArray()

  $stream.Dispose()
  $pen.Dispose()
  $backgroundBrush.Dispose()
  $backgroundPath.Dispose()
  $mailPath.Dispose()
  $graphics.Dispose()
  $bitmap.Dispose()

  return ,$bytes
}

$pngEntries = foreach ($size in $Sizes) {
  [PSCustomObject]@{
    Size = $size
    Bytes = [byte[]](New-LogoPngBytes $size)
  }
}

$directorySize = 6 + ($pngEntries.Count * 16)
$imageOffset = $directorySize
$iconStream = [System.IO.MemoryStream]::new()
$writer = [System.IO.BinaryWriter]::new($iconStream)

$writer.Write([UInt16]0)
$writer.Write([UInt16]1)
$writer.Write([UInt16]$pngEntries.Count)

foreach ($entry in $pngEntries) {
  $dimensionByte = if ($entry.Size -eq 256) { 0 } else { $entry.Size }
  $writer.Write([byte]$dimensionByte)
  $writer.Write([byte]$dimensionByte)
  $writer.Write([byte]0)
  $writer.Write([byte]0)
  $writer.Write([UInt16]1)
  $writer.Write([UInt16]32)
  $writer.Write([UInt32]$entry.Bytes.Length)
  $writer.Write([UInt32]$imageOffset)
  $imageOffset += $entry.Bytes.Length
}

foreach ($entry in $pngEntries) {
  $writer.Write($entry.Bytes)
}

$writer.Flush()
[System.IO.Directory]::CreateDirectory((Split-Path -Parent $OutputPath)) | Out-Null
[System.IO.File]::WriteAllBytes($OutputPath, $iconStream.ToArray())

$writer.Dispose()
$iconStream.Dispose()

Write-Host "Generated $OutputPath"
