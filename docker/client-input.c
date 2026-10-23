#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static HWND game;
static BOOL CALLBACK find_game(HWND window, LPARAM unused) {
    char title[256]; GetWindowTextA(window, title, sizeof(title));
    if (!strcmp(title, "Rusty Hearts Online ") || !strcmp(title, "RustyHearts Online ")) {
        game=window; return FALSE;
    }
    return TRUE;
}
static void key(WORD vk, BOOL up) {
    INPUT input={0}; input.type=INPUT_KEYBOARD; input.ki.wVk=vk;
    input.ki.dwFlags=up ? KEYEVENTF_KEYUP : 0; SendInput(1,&input,sizeof(input));
}
int main(int argc,char **argv) {
    EnumWindows(find_game,0);
    if (!game) EnumChildWindows(GetDesktopWindow(),find_game,0);
    if (!game) { fputs("Game window not found\n",stderr); return 2; }
    for (int i=1;i<argc;) {
        SetForegroundWindow(game); BringWindowToTop(game); SetFocus(game); Sleep(200);
        if (!strcmp(argv[i],"click") && i+2<argc) {
            POINT p={atoi(argv[i+1]),atoi(argv[i+2])}; ClientToScreen(game,&p);
            SetCursorPos(p.x,p.y); Sleep(150);
            mouse_event(MOUSEEVENTF_LEFTDOWN,0,0,0,0); Sleep(80);
            mouse_event(MOUSEEVENTF_LEFTUP,0,0,0,0); i+=3;
        } else if (!strcmp(argv[i],"type") && i+1<argc) {
            for (const char *p=argv[i+1];*p;++p) {
                SHORT code=VkKeyScanA(*p); if (code==-1) return 3;
                if (HIBYTE(code)&1) key(VK_SHIFT,FALSE);
                key(LOBYTE(code),FALSE); Sleep(50); key(LOBYTE(code),TRUE);
                if (HIBYTE(code)&1) key(VK_SHIFT,TRUE);
                Sleep(60);
            }
            i+=2;
        } else if (!strcmp(argv[i],"key") && i+1<argc) {
            WORD vk=!strcmp(argv[i+1],"TAB")?VK_TAB:!strcmp(argv[i+1],"ESC")?VK_ESCAPE:VK_RETURN;
            key(vk,FALSE); Sleep(100); key(vk,TRUE); i+=2;
        } else return 4;
        Sleep(300);
    }
    return 0;
}
