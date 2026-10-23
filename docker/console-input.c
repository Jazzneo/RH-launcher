#define UNICODE
#define _UNICODE
#include <windows.h>
#include <tlhelp32.h>
#include <stdio.h>

static int dump_console(const wchar_t *path) {
    HANDLE output = CreateFileW(L"CONOUT$", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    CONSOLE_SCREEN_BUFFER_INFO info;
    if (output == INVALID_HANDLE_VALUE || !GetConsoleScreenBufferInfo(output, &info)) return 11;
    FILE *file = _wfopen(path, L"wb");
    if (!file) { CloseHandle(output); return 12; }
    int first = info.dwCursorPosition.Y > 200 ? info.dwCursorPosition.Y - 200 : 0;
    for (int y=first; y<=info.dwCursorPosition.Y; ++y) {
        WCHAR row[4096]; DWORD count=0; COORD position = {0, (SHORT)y};
        DWORD width = info.dwSize.X < 4095 ? info.dwSize.X : 4095;
        if (ReadConsoleOutputCharacterW(output, row, width, position, &count)) {
            while (count && row[count-1] == L' ') --count;
            char utf8[16384];
            int size = WideCharToMultiByte(CP_UTF8, 0, row, count, utf8, sizeof(utf8), NULL, NULL);
            fwrite(utf8, 1, size, file); fputc('\n', file);
        }
    }
    fclose(file); CloseHandle(output); return 0;
}

int wmain(int argc, wchar_t **argv) {
    if (argc >= 3 && !_wcsicmp(argv[1], L"--launch")) {
        STARTUPINFOW info = {0}; info.cb = sizeof(info);
        PROCESS_INFORMATION child = {0};
        wchar_t command[MAX_PATH * 2];
        _snwprintf(command, MAX_PATH * 2, L"\"%ls\"", argv[2]);
        if (!CreateProcessW(argv[2], command, NULL, NULL, FALSE, CREATE_NEW_CONSOLE, NULL, NULL, &info, &child)) {
            fprintf(stderr, "CreateProcess failed: %lu\n", GetLastError()); return 7;
        }
        if (argc == 4) {
            FreeConsole();
            BOOL attached = FALSE;
            for (int i=0; i<40; ++i) {
                if (AttachConsole(child.dwProcessId)) { attached=TRUE; break; }
                if (WaitForSingleObject(child.hProcess, 250) != WAIT_TIMEOUT) break;
            }
            if (!attached) { fprintf(stderr, "Child console unavailable: %lu\n", GetLastError()); return 8; }
            Sleep(3000);
            HANDLE input = CreateFileW(L"CONIN$", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
            if (input == INVALID_HANDLE_VALUE) return 9;
            for (wchar_t *p=argv[3]; *p; ++p) {
                INPUT_RECORD records[2] = {0};
                for (int i=0; i<2; ++i) {
                    records[i].EventType=KEY_EVENT;
                    records[i].Event.KeyEvent.bKeyDown=i==0;
                    records[i].Event.KeyEvent.wRepeatCount=1;
                    records[i].Event.KeyEvent.wVirtualKeyCode=LOBYTE(VkKeyScanW(*p));
                    records[i].Event.KeyEvent.uChar.UnicodeChar=*p;
                }
                DWORD written;
                if (!WriteConsoleInputW(input,records,2,&written)) return 10;
                Sleep(400);
            }
            CloseHandle(input);
        }
        WaitForSingleObject(child.hProcess, INFINITE);
        DWORD status=1; GetExitCodeProcess(child.hProcess,&status);
        if (status && argc == 4) dump_console(L"Z:\\tmp\\rh-agent-exit.txt");
        CloseHandle(child.hThread); CloseHandle(child.hProcess); return status;
    }
    BOOL dump = argc == 4 && !_wcsicmp(argv[1], L"--dump");
    if (!dump && argc != 3) return 2;
    wchar_t *process_name = argv[dump ? 2 : 1];
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    PROCESSENTRY32W entry = {0}; entry.dwSize = sizeof(entry);
    DWORD pid = 0;
    if (Process32FirstW(snapshot, &entry)) do {
        if (!_wcsicmp(entry.szExeFile, process_name)) { pid = entry.th32ProcessID; break; }
    } while (Process32NextW(snapshot, &entry));
    CloseHandle(snapshot);
    if (!pid) { fwprintf(stderr, L"Process not found: %ls\n", process_name); return 3; }
    FreeConsole();
    if (!AttachConsole(pid)) { fprintf(stderr, "AttachConsole failed: %lu\n", GetLastError()); return 4; }
    if (dump) {
        int result = dump_console(argv[3]); FreeConsole(); return result;
    }
    HANDLE input = CreateFileW(L"CONIN$", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    if (input == INVALID_HANDLE_VALUE) return 5;
    for (wchar_t *p = argv[2]; *p; ++p) {
        SHORT vk = VkKeyScanW(*p);
        INPUT_RECORD records[2] = {0};
        for (int i=0; i<2; ++i) {
            records[i].EventType = KEY_EVENT;
            records[i].Event.KeyEvent.bKeyDown = i == 0;
            records[i].Event.KeyEvent.wRepeatCount = 1;
            records[i].Event.KeyEvent.wVirtualKeyCode = LOBYTE(vk);
            records[i].Event.KeyEvent.wVirtualScanCode = MapVirtualKeyW(LOBYTE(vk), MAPVK_VK_TO_VSC);
            records[i].Event.KeyEvent.uChar.UnicodeChar = *p;
        }
        DWORD written = 0;
        if (!WriteConsoleInputW(input, records, 2, &written)) return 6;
        Sleep(400);
    }
    CloseHandle(input); FreeConsole(); return 0;
}
