#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <ddeml.h>
#include <stdio.h>
#include <string.h>

static int failures;
static int advises;
static HDDEDATA CALLBACK callback(UINT type, UINT format, HCONV conversation,
                                 HSZ topic, HSZ item, HDDEDATA data,
                                 ULONG_PTR arg1, ULONG_PTR arg2)
{
    char text[128] = {0};
    if ((type & ~0x000c) == XTYP_ADVDATA) {
        DWORD copied = DdeGetData(data, (LPBYTE)text, sizeof(text), 0);
        if (!copied || strcmp(text, "SNACA314 AZ348.4 EL0.5 UP0 UMUSB DN0 DMUSB MA0.0") != 0)
            ++failures;
        ++advises;
        return (HDDEDATA)DDE_FACK;
    }
    return 0;
}

int main(int argc, char **argv)
{
    DWORD instance = 0;
    HSZ service, topic, item;
    HCONV conversation;
    MSG message;
    int i;
    if (argc < 2 || strncmp(argv[1], "AirWSJTX-Test-", 14) != 0)
        return 2;
    if (DdeInitializeA(&instance, callback, APPCMD_CLIENTONLY, 0) != DMLERR_NO_ERROR)
        return 3;
    service = DdeCreateStringHandleA(instance, argv[1], CP_WINANSI);
    topic = DdeCreateStringHandleA(instance, "SatPcDdeConv", CP_WINANSI);
    item = DdeCreateStringHandleA(instance, "SatPcDdeItem", CP_WINANSI);
    conversation = DdeConnect(instance, service, topic, NULL);
    if (!conversation)
        return 4;
    if (!DdeClientTransaction(NULL, 0, conversation, item, CF_TEXT,
                              XTYP_ADVSTART | (argc > 2 ? 0 : XTYPF_ACKREQ), 2000, NULL))
        return 5;
    for (i = 0; i < 100; ++i) {
        char text[128] = {0};
        HDDEDATA data = DdeClientTransaction(NULL, 0, conversation, item,
                                            CF_TEXT, XTYP_REQUEST, 2000, NULL);
        DWORD copied = data ? DdeGetData(data, (LPBYTE)text, sizeof(text), 0) : 0;
        if (!copied || strcmp(text, "SNACA314 AZ348.4 EL0.5 UP0 UMUSB DN0 DMUSB MA0.0") != 0) {
            printf("BAD handle=%p copied=%lu error=%x text=%s\n",
                   data, copied, DdeGetLastError(instance), text);
            ++failures;
        }
        if (data)
            DdeFreeDataHandle(data);
        while (PeekMessage(&message, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&message);
            DispatchMessage(&message);
        }
        Sleep(20);
    }
    /* A dual-mode client polls first, then must resume receiving hot advice. */
    {
        DWORD until = GetTickCount() + 1500;
        while (GetTickCount() < until) {
            while (PeekMessage(&message, NULL, 0, 0, PM_REMOVE)) {
                TranslateMessage(&message);
                DispatchMessage(&message);
            }
            Sleep(10);
        }
    }
    DdeDisconnect(conversation);
    DdeFreeStringHandle(instance, item);
    DdeFreeStringHandle(instance, topic);
    DdeFreeStringHandle(instance, service);
    DdeUninitialize(instance);
    printf("requests=100 advises=%d failures=%d\n", advises, failures);
    return failures || !advises;
}
