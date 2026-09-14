#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>

#define PAGE 4096

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: %s <pid>\n", argv[0]); return 2; }
    int pid = atoi(argv[1]);

    char mp[64], pm[64];
    snprintf(mp, sizeof(mp), "/proc/%d/maps", pid);
    snprintf(pm, sizeof(pm), "/proc/%d/pagemap", pid);

    FILE *maps = fopen(mp, "r");
    int pmfd = open(pm, O_RDONLY);
    if (!maps || pmfd < 0) { perror("open"); return 1; }

    printf("PID %d — live page table walk\n\n", pid);
    printf("%-18s %-5s %-6s %-6s %-6s %-6s %-6s %-6s\n",
           "virtual address", "perm", "state", "PGD", "PUD", "PMD", "PTE", "off");

    char line[512];
    int rows = 0;
    while (fgets(line, sizeof(line), maps) && rows < 15) {
        unsigned long start, end;
        char perms[8];
        if (sscanf(line, "%lx-%lx %7s", &start, &end, perms) != 3) continue;
        if (!strchr(perms, 'r')) continue;

        /* sample 3 pages from this VMA */
        unsigned long step = (end - start) / 4;
        if (step < PAGE) step = PAGE;
        for (unsigned long va = start; va < end && rows < 15; va += step) {
            off_t off = ((va / PAGE) * 8);
            uint64_t e = 0;
            if (pread(pmfd, &e, 8, off) != 8) continue;
            int present = (e >> 63) & 1;
            int swapped = (e >> 62) & 1;
            printf("0x%016lx %-5s %-6s 0x%03x  0x%03x  0x%03x  0x%03x  0x%03x\n",
                   va, perms, present ? "RAM" : (swapped ? "SWAP" : "---"),
                   (int)((va >> 39) & 0x1FF), (int)((va >> 30) & 0x1FF),
                   (int)((va >> 21) & 0x1FF), (int)((va >> 12) & 0x1FF),
                   (int)(va & 0xFFF));
            rows++;
        }
    }
    fclose(maps); close(pmfd);
    return 0;
}
