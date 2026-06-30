#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void handle(char *dst, const char *src) {
    strcpy(dst, src);
    char buffer[16];
    gets(buffer);
    system("ls");
}
