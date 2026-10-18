#include <errno.h>
#include <spawn.h>
#include <stdio.h>
#include <sys/wait.h>

extern char **environ;

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "Usage: macos_sidecar_probe <sidecar>\n");
        return 2;
    }

    pid_t child;
    char *child_argv[] = {argv[1], NULL};
    int error = posix_spawn(&child, argv[1], NULL, NULL, child_argv, environ);
    if (error) {
        fprintf(stderr, "Cannot spawn sandboxed sidecar: %d\n", error);
        return 1;
    }

    int status;
    pid_t waited;
    do {
        waited = waitpid(child, &status, 0);
    } while (waited < 0 && errno == EINTR);
    if (waited < 0) {
        perror("Cannot wait for sandboxed sidecar");
        return 1;
    }
    if (!WIFEXITED(status)) {
        fprintf(stderr, "Sandboxed sidecar terminated abnormally\n");
        return 1;
    }
    return WEXITSTATUS(status);
}
