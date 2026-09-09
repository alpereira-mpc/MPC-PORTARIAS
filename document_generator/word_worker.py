"""Isolated COM worker. DispatchEx never attaches to the user's open Word instance."""

import sys
import pythoncom
import win32com.client


def main(source, target):
    pythoncom.CoInitialize()
    word = document = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = 3
        document = word.Documents.Open(source, ReadOnly=True, AddToRecentFiles=False)
        document.ExportAsFixedFormat(target, 17, OpenAfterExport=False)
    finally:
        if document is not None:
            document.Close(False)
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
