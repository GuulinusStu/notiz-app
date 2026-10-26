# Spricht mit der OneNote-App am PC (Microsoft 365 / OneNote 2016) über ihre COM-Schnittstelle.
#   -Action list   -OutFile x.json              Alle Abschnitte als JSON auflisten
#   -Action export -SectionId ID -OutFile x.pdf Einen Abschnitt als PDF exportieren
#   Exit-Codes: 2 = OneNote fehlt, 3 = Abschnitt leer, 4 = noch nicht vollständig heruntergeladen
param(
    [Parameter(Mandatory = $true)][ValidateSet("list", "export")][string]$Action,
    [Parameter(Mandatory = $true)][string]$OutFile,
    [string]$SectionId,
    [int]$WaitSeconds = 60
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

    # Sicherstellen, dass alle Seiten wirklich auf dem PC sind. Seiten aus der Cloud, die OneNote noch
    # nicht heruntergeladen hat, lassen sich nicht lesen. Dann warten wir kurz und brechen sonst ab,
    # statt einen halb leeren Abschnitt zu exportieren.
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ($true) {
        $missing = @()
        try {
            $pagesXml = ""
            $onenote.GetHierarchy($SectionId, 4, [ref]$pagesXml)   # 4 = hsPages
            [xml]$px = $pagesXml
            $pns = New-Object System.Xml.XmlNamespaceManager($px.NameTable)
            $pns.AddNamespace("one", $px.DocumentElement.NamespaceURI)
            $pages = @($px.SelectNodes("//one:Page", $pns))
        } catch {
            $pages = @()
            $missing += "Seitenliste"
        }
        foreach ($page in $pages) {
            try {
                $content = ""
                $onenote.GetPageContent($page.ID, [ref]$content, 0)
                if (-not $content) { $missing += $page.name }
            } catch {
                $missing += $page.name
            }
        }
        if ($missing.Count -eq 0) { break }
        if ((Get-Date) -gt $deadline) {
            [Console]::Error.WriteLine("Noch nicht vollständig heruntergeladen: " + (($missing | Select-Object -First 3) -join ", "))
            exit 4
        }
        Start-Sleep -Seconds 3
        try { $onenote.SyncHierarchy($SectionId) } catch { }
    }
    [Console]::Out.WriteLine("PAGES=" + $pages.Count)
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
