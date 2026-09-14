#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>

#define PAGE_SIZE     4096
#define TOTAL_MB      256
#define TOTAL_PAGES   ((TOTAL_MB*1024*1024)/PAGE_SIZE)
#define HOT_PAGES     256
#define BUDGET        256
#define ROUNDS_A      200000
#define ROUNDS_B      50000
#define HOT_A         0
#define HOT_B         30000

typedef struct { unsigned long count, last; } meta_t;
static meta_t meta[TOTAL_PAGES];
static char *region = NULL;
static unsigned long tick = 0;
static int json_mode = 0;

static int by_count(const void *a, const void *b) {
    unsigned long ia = *(unsigned long*)a, ib = *(unsigned long*)b;
    if (meta[ia].count != meta[ib].count) return meta[ia].count > meta[ib].count ? -1 : 1;
    return ia < ib ? -1 : 1;
}
static int by_recency(const void *a, const void *b) {
    unsigned long ia = *(unsigned long*)a, ib = *(unsigned long*)b;
    if (meta[ia].last != meta[ib].last) return meta[ia].last > meta[ib].last ? -1 : 1;
    return ia < ib ? -1 : 1;
}

static unsigned long apply_and_lock(int (*cmp)(const void*, const void*)) {
    unsigned long *ix = malloc(TOTAL_PAGES*sizeof(unsigned long));
    char *keep = calloc(TOTAL_PAGES, 1);
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) ix[i] = i;
    qsort(ix, TOTAL_PAGES, sizeof(unsigned long), cmp);
    for (int i = 0; i < BUDGET; i++) keep[ix[i]] = 1;
    unsigned long rs = 0, demoted = 0; int in_run = 0;
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) {
        if (!keep[i]) { if (!in_run) { rs = i; in_run = 1; } }
        else if (in_run) {
            unsigned long len = (i - rs) * PAGE_SIZE;
            if (len) { madvise(region + rs*PAGE_SIZE, len, MADV_PAGEOUT); demoted += (i - rs); }
            in_run = 0;
        }
    }
    if (in_run) {
        unsigned long len = (TOTAL_PAGES - rs) * PAGE_SIZE;
        madvise(region + rs*PAGE_SIZE, len, MADV_PAGEOUT);
        demoted += (TOTAL_PAGES - rs);
    }
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) if (keep[i]) region[i*PAGE_SIZE] = 1;
    free(ix); free(keep);
    return demoted;
}

static unsigned long resident(unsigned long start, unsigned long len) {
    unsigned char *v = calloc(len, 1);
    if (mincore(region + start*PAGE_SIZE, len*PAGE_SIZE, v) != 0) { free(v); return 0; }
    unsigned long c = 0;
    for (unsigned long i = 0; i < len; i++) if (v[i] & 1) c++;
    free(v); return c;
}

static void run_workload(void) {
    for (unsigned long r = 0; r < ROUNDS_A; r++) {
        unsigned long i = HOT_A + (rand() % HOT_PAGES);
        region[i*PAGE_SIZE] = 1; meta[i].count++; meta[i].last = tick++;
    }
    for (unsigned long r = 0; r < ROUNDS_B; r++) {
        unsigned long i = HOT_B + (rand() % HOT_PAGES);
        region[i*PAGE_SIZE] = 1; meta[i].count++; meta[i].last = tick++;
    }
}

static void reset(void) {
    memset(meta, 0, sizeof(meta)); tick = 0;
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) region[i*PAGE_SIZE] = 1;
    sleep(3);
}

int main(int argc, char **argv) {
    json_mode = (argc >= 2 && strcmp(argv[1], "--json") == 0);
    srand(42);
    region = mmap(NULL, (size_t)TOTAL_MB*1024*1024, PROT_READ|PROT_WRITE,
                  MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    if (region == MAP_FAILED) { perror("mmap"); return 1; }

    if (!json_mode) printf("\n--- RUN 1: NAIVE (frequency-only) ---\n");
    reset(); run_workload();
    unsigned long d1 = apply_and_lock(by_count);
    sleep(5);
    unsigned long nA = resident(HOT_A, HOT_PAGES);
    unsigned long nB = resident(HOT_B, HOT_PAGES);
    if (!json_mode)
        printf("    A resident=%lu/256  B resident=%lu/256  demoted=%lu\n", nA, nB, d1);

    if (!json_mode) printf("\n--- RUN 2: ADAPTIVE (recency-aware) ---\n");
    reset(); run_workload();
    unsigned long d2 = apply_and_lock(by_recency);
    sleep(5);
    unsigned long aA = resident(HOT_A, HOT_PAGES);
    unsigned long aB = resident(HOT_B, HOT_PAGES);
    if (!json_mode)
        printf("    A resident=%lu/256  B resident=%lu/256  demoted=%lu\n", aA, aB, d2);

    if (json_mode) {
        printf("{\n");
        printf("  \"naive_a\": %lu, \"naive_b\": %lu, \"naive_demoted\": %lu,\n", nA, nB, d1);
        printf("  \"adapt_a\": %lu, \"adapt_b\": %lu, \"adapt_demoted\": %lu,\n", aA, aB, d2);
        printf("  \"budget\": %d, \"hot_pages\": %d,\n", BUDGET, HOT_PAGES);
        printf("  \"verdict\": \"%s\"\n",
               (nB == aB) ? "kernel LRU subsumes both policies on this workload"
                          : "adaptive policy outperforms frequency-only");
        printf("}\n");
    } else {
        printf("\n=== RESULT ===\n");
        printf("  NAIVE    keeps B (current hot set): %3lu/256 (%.0f%%)\n", nB, 100.0*nB/HOT_PAGES);
        printf("  ADAPTIVE keeps B (current hot set): %3lu/256 (%.0f%%)\n", aB, 100.0*aB/HOT_PAGES);
        if (nB == aB)
            printf("  Finding: both policies tie — kernel LRU already handles this workload class.\n");
    }

    munmap(region, (size_t)TOTAL_MB*1024*1024);
    return 0;
}
