"""Turn .txt / .epub novels into an archive the app's Import button accepts.

    python AReaderImportBuilder <file or folder> -o my-books.zip
    python AReaderImportBuilder <file or folder> --dry-run   # show the split only

Double-click `AReader Import Builder.bat` (or `gui.pyw`) for the window version.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli import main

if __name__ == "__main__":
    raise SystemExit(main())
