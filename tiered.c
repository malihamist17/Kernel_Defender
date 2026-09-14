#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>

#define PAGE_SIZE     4096
#define TOTAL_MB      256
#define TOTAL_PAGES   ((TOTAL_MB * 1024 * 1024) / PAGE_SIZE)
#define HOT_PAGES     256
#define ACCESS_ROUNDS 200000
#define HOT_THRESHOLD 50

typedef struct { unsigned long access_count; unsigned long last_access_round; } page_meta_t;
static page_meta_t meta[TOTAL_PAGES];
static char *region = NULL;
static unsigned long current_round = 0;

static long read_vmstat(const char *key) {
    FILE *f = fopen("/proc/vmstat", "r");
    if (!f) return -1;
    char k[64]; long v;
    while (fscanf(f, "%63s %ld", k, &v) == 2)
        if (strcmp(k, key) == 0) { fclose(f); return v; }
    fclose(f); return -1;
}

static void read_self_status(const char *label) {
    FILE *f = fopen("/proc/self/status", "r");
    if (!f) return;
    char line[256]; long rss = -1, swp = -1;
    while (fgets(line, sizeof(line), f)) {
        if (strncmp(line, "VmRSS:", 6) == 0) rss = atol(line + 6);
        if (strncmp(line, "VmSwap:", 7) == 0) swp = atol(line + 7);
    }
    fclose(f);
    printf("  [%s] VmRSS=%ld kB   VmSwap=%ld kB\n", label, rss, swp);
}

static void touch_page(unsigned long i) {
    region[i * PAGE_SIZE] = (char)(i & 0xFF);
    meta[i].access_count++;
    meta[i].last_access_round = current_round;
}

static void workload_skewed(unsigned long rounds) {
    for (unsigned long r = 0; r < rounds; r++) {
        current_round = r;
        if ((rand() % 100) < 80) touch_page(rand() % HOT_PAGES);
        else                     touch_page(rand() % TOTAL_PAGES);
    }
}

static void policy_baseline(void) {
    printf("  [policy] baseline: no action (pages stay in RAM)\n");
}

static void policy_adaptive(void) {
    printf("  [policy] adaptive: scanning %lu pages for demotion...\n", (unsigned long)TOTAL_PAGES);
    unsigned long demoted = 0, run_start = 0; int in_run = 0;
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) {
        int hot = (meta[i].access_count >= HOT_THRESHOLD) && (current_round - meta[i].last_access_round < 1000);
        if (!hot) { if (!in_run) { run_start = i; in_run = 1; } }
        else if (in_run) {
            unsigned long len = (i - run_start) * PAGE_SIZE;
            if (len > 0) { madvise(region + run_start * PAGE_SIZE, len, MADV_PAGEOUT); demoted += (i - run_start); }
            in_run = 0;
        }
    }
    if (in_run) {
        unsigned long len = (TOTAL_PAGES - run_start) * PAGE_SIZE;
        madvise(region + run_start * PAGE_SIZE, len, MADV_PAGEOUT);
        demoted += (TOTAL_PAGES - run_start);
    }
    printf("  [policy] adaptive: queued %lu pages for demotion\n", demoted);
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: %s <baseline|adaptive>\n", argv[0]); return 2; }
    int adaptive = strcmp(argv[1], "adaptive") == 0;
    srand(42);
    printf("\n=== Tiered Memory Demo: %s ===\n\n", argv[1]);

    region = mmap(NULL, (size_t)TOTAL_MB * 1024 * 1024, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (region == MAP_FAILED) { perror("mmap"); return 1; }
    memset(meta, 0, sizeof(meta));

    printf("[1] Allocated %d MB anonymous memory (%lu pages)\n", TOTAL_MB, (unsigned long)TOTAL_PAGES);
    read_self_status("after alloc");

    printf("\n[2] Touching every page to force residency...\n");
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) region[i * PAGE_SIZE] = 1;
    read_self_status("after first touch");

    printf("\n[3] Running skewed workload (%d accesses)...\n", ACCESS_ROUNDS);
    workload_skewed(ACCESS_ROUNDS);
    read_self_status("after workload");

    long pswpout_before = read_vmstat("pswpout");
    long pswpin_before  = read_vmstat("pswpin");

    printf("\n[4] Applying policy...\n");
    if (adaptive) policy_adaptive(); else policy_baseline();

    printf("\n[5] Sleeping 3s to let kernel complete writeback...\n");
    sleep(3);
    read_self_status("after policy");

    long pswpout_after = read_vmstat("pswpout");
    long pswpin_after  = read_vmstat("pswpin");
    printf("  [vmstat delta] pswpout: %+ld pages   pswpin: %+ld pages\n",
           pswpout_after - pswpout_before, pswpin_after - pswpin_before);

    printf("\n=== Done ===\n");
    munmap(region, (size_t)TOTAL_MB * 1024 * 1024);
    return 0;
}
