#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>

#define PAGE 4096
#define TOTAL_MB 128
#define TOTAL_PAGES ((TOTAL_MB * 1024 * 1024) / PAGE)
#define HOT_COUNT 256
#define HOT_START 15000
#define HOT_WARM 20000
#define EXTRA_MB 40

static char *region = NULL;

static void touch(unsigned long i) { region[i * PAGE] = (char)(i & 0xFF); }

static unsigned long resident(unsigned long start, unsigned long len) {
    unsigned char *v = calloc(len, 1);
    if (mincore(region + start * PAGE, len * PAGE, v) != 0) { free(v); return 0; }
    unsigned long c = 0;
    for (unsigned long i = 0; i < len; i++) if (v[i] & 1) c++;
    free(v); return c;
}

int main(void) {
    srand(42);
    region = mmap(NULL, (size_t)TOTAL_MB * 1024 * 1024,
                  PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (region == MAP_FAILED) { perror("mmap"); return 1; }

    printf("[1] Committing %d pages (%d MB)\n", TOTAL_PAGES, TOTAL_MB);
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) touch(i);
    sleep(2);

    printf("[2] Warming hot set (pages %d-%d) x %d rounds\n",
           HOT_START, HOT_START + HOT_COUNT - 1, HOT_WARM);
    for (int r = 0; r < HOT_WARM; r++) touch(HOT_START + (rand() % HOT_COUNT));
    printf("    hot resident after warm: %lu / %d\n",
           resident(HOT_START, HOT_COUNT), HOT_COUNT);

    printf("[3] Sequential scan of other pages\n");
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) {
        if (i >= HOT_START && i < HOT_START + HOT_COUNT) continue;
        touch(i);
    }
    printf("    scan complete. hot resident: %lu\n", resident(HOT_START, HOT_COUNT));

    printf("[4] Allocating %d MB extra to force eviction\n", EXTRA_MB);
    size_t mb = EXTRA_MB;
    char *p = mmap(NULL, mb*1024*1024, PROT_READ|PROT_WRITE,
                   MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    if (p != MAP_FAILED) for (size_t i = 0; i < mb*1024*1024; i += PAGE) p[i] = 1;
    sleep(5);

    unsigned long hot_after  = resident(HOT_START, HOT_COUNT);
    unsigned long scan_start = resident(0, 2000);
    unsigned long scan_mid   = resident(HOT_START/2, 2000);
    unsigned long scan_end   = resident(TOTAL_PAGES - 2000, 2000);

    printf("\n=== RESULT ===\n");
    printf("  Hot set (warm, freq > 20000)      resident: %lu / %d\n", hot_after, HOT_COUNT);
    printf("  Scan start (freq = 1, earliest)   resident: %lu / 2000\n", scan_start);
    printf("  Scan mid (freq = 1, middle)       resident: %lu / 2000\n", scan_mid);
    printf("  Scan end (freq = 1, latest)       resident: %lu / 2000\n", scan_end);

    printf("\n=== INTERPRETATION ===\n");
    if (hot_after >= 200 && scan_end < 500) {
        printf("  Kernel PROTECTED the hot set. MGLRU handled this workload.\n");
    } else if (hot_after < 100 && scan_end >= 500) {
        printf("  Hot set EVICTED, scan pages survive. RECENCY beat FREQUENCY.\n");
        printf("  This is the LRU failure mode our policy would prevent.\n");
    } else {
        printf("  Mixed. Hot=%lu/256, scan_end=%lu/2000.\n", hot_after, scan_end);
    }

    if (p != MAP_FAILED) munmap(p, mb*1024*1024);
    munmap(region, (size_t)TOTAL_MB * 1024 * 1024);
    return 0;
}
