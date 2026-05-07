$repo = "c:\Users\asliu\Stock Screener"
Set-Location $repo

$status = git status --porcelain 2>&1
if ($status) {
    git add app.py templates/index.html
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm"
    git commit -m "Auto-backup $ts"
    git push origin master
    "[$ts] Backup pushed." >> "$repo\backup.log"
} else {
    "[ $(Get-Date -Format 'yyyy-MM-dd HH:mm') ] No changes." >> "$repo\backup.log"
}
