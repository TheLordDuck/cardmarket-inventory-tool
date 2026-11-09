Cardmarket Inventory Tool
=========================

Requirements: Windows 10/11 and Google Chrome installed. No Python needed.

1. Unzip this archive into a folder (don't run it from inside the zip).
2. (Optional) Copy ".env.example" to ".env" in this same folder and fill in
   CARDMARKET_USERNAME and CARDMARKET_PASSWORD so the login form gets filled
   in for you. Otherwise, log in yourself in the Chrome window that opens.
   You always complete 2FA / captcha yourself.
3. Run CardmarketInventoryTool.exe.

CSV exports and logs are saved in the "reports" and "logs" folders next to
the .exe. Your Chrome session is kept in ".cardmarket-browser-profile"
(never share that folder or your .env: they hold your session and password).

Windows SmartScreen may warn you because the .exe isn't signed:
"More info" -> "Run anyway".
