# Spricht mit der OneNote-App am PC (Microsoft 365 / OneNote 2016) über ihre COM-Schnittstelle.
#   -Action list   -OutFile x.json              Alle Abschnitte als JSON auflisten
#   -Action export -SectionId ID -OutFile x.pdf Einen Abschnitt als PDF exportieren
param(
    [Parameter(Mandatory = $true)][ValidateSet("list", "export")][string]$Action,
    [Parameter(Mandatory = $true)][string]$OutFile,
    [string]$SectionId
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Get-ErrorCode($err) {
    $ex = $err.Exception
    while ($ex.InnerException) { $ex = $ex.InnerException }
    return @(('{0:X8}' -f $ex.HResult), $ex.Message.Trim())
}

try {
    $onenote = New-Object -ComObject OneNote.Application
} catch {
    [Console]::Error.WriteLine("Die OneNote-App für den PC wurde nicht gefunden. Bitte OneNote (aus Microsoft 365) installieren und einmal öffnen.")
    exit 2
}

if ($Action -eq "list") {
    $xmlText = ""
    $onenote.GetHierarchy("", 3, [ref]$xmlText)   # 3 = hsSections
    [xml]$xml = $xmlText
    $ns = New-Object System.Xml.XmlNamespaceManager($xml.NameTable)
    $ns.AddNamespace("one", $xml.DocumentElement.NamespaceURI)

    $sections = @()
    foreach ($section in $xml.SelectNodes("//one:Section", $ns)) {
        if ($section.isInRecycleBin -eq "true" -or $section.locked -eq "true" -or $section.encrypted -eq "true") { continue }
        $names = @()
        $skip = $false
        $node = $section.ParentNode
        while ($node -ne $null -and $node.LocalName -ne "Notebooks") {
            if ($node.isRecycleBin -eq "true") { $skip = $true }
            if ($node.name) { $names = @($node.name) + $names }
            $node = $node.ParentNode
        }
        if ($skip) { continue }
        $sections += [pscustomobject]@{
            id           = $section.ID
            name         = $section.name
            notebook     = if ($names.Count -gt 0) { $names[0] } else { "" }
            path         = (@($names) + @($section.name)) -join " / "
            lastModified = $section.lastModifiedTime
        }
    }
    ConvertTo-Json -InputObject @($sections) -Depth 3 | Out-File -FilePath $OutFile -Encoding utf8
    exit 0
}

if ($Action -eq "export") {
    if (-not $SectionId) { [Console]::Error.WriteLine("SectionId fehlt"); exit 1 }
    try { $onenote.SyncHierarchy($SectionId) } catch { }   # neueste Version holen, falls möglich
    if (Test-Path -LiteralPath $OutFile) { Remove-Item -LiteralPath $OutFile -Force }
    try {
        $onenote.Publish($SectionId, $OutFile, 3, "")      # 3 = pfPDF
    } catch {
        $code, $message = Get-ErrorCode $_
        if ($code -eq "80042006") {                        # OneNote exportiert leere Abschnitte nicht
            [Console]::Error.WriteLine("Abschnitt ist leer")
            exit 3
        }
        [Console]::Error.WriteLine("Export fehlgeschlagen (0x$code): $message")
        exit 1
    }
    exit 0
}
