#include <sys/types.h>
#include <sys/stat.h>
#include <dirent.h>
#include <err.h>
#include <errno.h>
#include <stdio.h>
#include <sysexits.h>
#include <unistd.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    DIR *dir = opendir(".");
    struct dirent *file;
    char *dirname;
    int links = 0, fixed = 0;

    while ((file = readdir(dir)) != NULL)
    {
        char target[1024];
        ssize_t index =
            readlink(file->d_name, target, sizeof(target) - 1);

        if (index < 0) {
            // Not a symlink?
            if (errno == EINVAL)
                continue;

            err(EX_OSERR, "error in readlink('%s')", file->d_name);
        }

        links++;

        // Fix absolute paths.
        if (target[0] == '/') {
            target[index] = 0;

            char *newName;
            asprintf(&newName, "../..%s", target);

            if (unlink(file->d_name))
                err(EX_OSERR, "Failed to remove old link");

            if (symlink(newName, file->d_name))
                err(EX_OSERR, "Failed to create link");
            free(newName);
            fixed++;
        }
    }
    closedir(dir);

    if (links == 0)
        errx(EX_USAGE, "no symbolic links in %s", getwd(NULL));

    printf("fixed %d/%d symbolic links\\n", fixed, links);
}
