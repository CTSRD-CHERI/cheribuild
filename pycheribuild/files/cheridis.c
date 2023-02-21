#include <stdio.h>
#include <string.h>

int main(int argc, char** argv)
{
    int i;
    int byte;

    FILE *dis = popen(LLVM_PATH "llvm-mc -disassemble -triple=cheri-unknown-freebsd", "w");
    for (i=1 ; i<argc ; i++)
    {
        char *inst = argv[i];
        if (strlen(inst) == 10)
        {
            if (inst[0] != '0' || inst[1] != 'x') continue;
            inst += 2;
        }
        else if (strlen(inst) != 8) continue;
        for (byte=0 ; byte<8 ; byte+=2)
        {
            fprintf(dis, "0x%.2s ", &inst[byte]);
        }
    }
    pclose(dis);
}
