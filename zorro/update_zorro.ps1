# Letolti a Jev fajlokat a GitHubrol a Zorro Strategy mappaba (mind egy helyre).
# A jev_key.txt, jev_cache.jsonl es a naplok erintetlenek maradnak.
param([string]$Strategy = "C:\Users\Administrator\Desktop\z12\Strategy")

$base = "https://raw.githubusercontent.com/Boddav/ai-trading-advisor/main/zorro"
$files = @(
  "server/JevServer.py", "server/start_jev.bat", "server/test_jev.bat", "server/prefetch_jev.bat",
  "server/jev_key.example.txt",
  "jevtrader/JevTrader.c", "deep/JevTradeDeep.c",
  "tools/calibrate_jev.py",
  "lotto/lotto_jev.py", "lotto/lotto_popularity.py"
)
if (-not (Test-Path $Strategy)) { Write-Host "Nincs ilyen mappa: $Strategy"; exit 1 }
foreach ($f in $files) {
  $name = Split-Path $f -Leaf
  try {
    Invoke-WebRequest "$base/$f" -OutFile (Join-Path $Strategy $name) -UseBasicParsing
    Write-Host "OK   $name"
  } catch {
    Write-Host "HIBA $name : $($_.Exception.Message)"
  }
}
if (-not (Test-Path (Join-Path $Strategy "jev_key.txt"))) {
  Write-Host "`nFIGYELEM: nincs jev_key.txt - nevezd at a jev_key.example.txt-t es ird bele a kulcsot."
}
Write-Host "`nKesz. A JevTrader.c / JevTradeDeep.c beallitasait (JEV_TEST_MODE, seed) ellenorizd, mert felulirodtak."
