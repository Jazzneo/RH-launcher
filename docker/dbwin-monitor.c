#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    FILE *file = fopen(argv[1], "wb");
    if (!file) return 3;
    HANDLE mapping = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 4096, "DBWIN_BUFFER");
    HANDLE ready = CreateEventA(NULL, FALSE, FALSE, "DBWIN_BUFFER_READY");
    HANDLE data = CreateEventA(NULL, FALSE, FALSE, "DBWIN_DATA_READY");
    if (!mapping || !ready || !data) return 4;
    char *buffer = MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, 4096);
    if (!buffer) return 5;
    ULONGLONG end = GetTickCount64() + (ULONGLONG)atoi(argv[2]) * 1000;
    fputs("Monitor ready\n", file); fflush(file);
    while (GetTickCount64() < end) {
        SetEvent(ready);
        if (WaitForSingleObject(data, 1000) == WAIT_OBJECT_0) {
            fprintf(file, "[%lu] %.*s\n", *(DWORD *)buffer, 4091, buffer+sizeof(DWORD));
            fflush(file);
        }
    }
    UnmapViewOfFile(buffer); CloseHandle(mapping); CloseHandle(ready); CloseHandle(data);
    fclose(file); return 0;
}
