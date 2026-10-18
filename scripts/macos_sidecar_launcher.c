#include <mach-o/dyld.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    (void)argc;
    char executable[PATH_MAX];
    uint32_t size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) != 0) {
        fprintf(stderr, "Cannot locate the sidecar launcher\n");
        return 1;
    }
    char *separator = strrchr(executable, '/');
    if (!separator) {
        fprintf(stderr, "Invalid sidecar launcher path\n");
        return 1;
    }
    *separator = '\0';

    char helper[PATH_MAX];
    int length = snprintf(helper, sizeof(helper),
        "%s/../Helpers/airwsjtx_bridge.app/Contents/MacOS/airwsjtx_bridge", executable);
    if (length < 0 || (size_t)length >= sizeof(helper)) {
        fprintf(stderr, "Python sidecar path is too long\n");
        return 1;
    }
    execv(helper, argv);
    perror("Cannot launch packaged Python sidecar");
    return 1;
}
