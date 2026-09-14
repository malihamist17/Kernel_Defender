#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>
#include <time.h>

#define PAGE_SIZE     4096
#define TOTAL_MB      256
#define TOTAL_PAGES   ((TOTAL_MB * 1024 * 1024) / PAGE_SIZE)
#define HOT_PAGES     256
#define ACCESS_ROUNDS 200000
#define HOT_THRESHOLD 50
#define DEMOTE_BATCH  512

typedef struct { unsigned long access_count; unsigned long last_access_round; } page_meta_t;
static page_meta_t meta[TOTAL_PAGES];
static char *region = NULL;
static unsigned long current_round = 0;

static void read_self_status(const char *label) {
    FILE *f = fopen("/proc/self/status", "r");
    if (!f) return;
    char line[256]; long rss = -1, swp = -1;
    while (fgets(line, sizeof(line), f)) {
        if (strncmp(line, "VmRSS:", 6) == 0) rss = atol(line + 6);
        if (strncmp(line, "VmSwap:", 7) == 0) swp = atol(line + 7);
    }
    fclose(f);
    fprintf(stderr, "  [%s] VmRSS=%ld kB   VmSwap=%ld kB\n", label, rss, swp);
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

static int page_is_hot(unsigned long i) {
    return meta[i].access_count >= HOT_THRESHOLD && (current_round - meta[i].last_access_round) < 1000;
}

static unsigned long run_demotion(void) {
    unsigned long demoted = 0, run_start = 0; int in_run = 0;
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) {
        if (!page_is_hot(i)) {
            if (!in_run) { run_start = i; in_run = 1; }
            if ((i - run_start + 1) >= DEMOTE_BATCH) {
                unsigned long len = (i - run_start + 1) * PAGE_SIZE;
                madvise(region + run_start * PAGE_SIZE, len, MADV_PAGEOUT);
                demoted += (i - run_start + 1); in_run = 0;
            }
        } else if (in_run) {
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
    return demoted;
}

/* Moderate pressure — lets the kernel make its own decisions,
   so the visualization shows real partial eviction. */
static void force_eviction(void) {
    size_t pressure_mb = 800;
    char *pressure = mmap(NULL, pressure_mb * 1024 * 1024,
                          PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (pressure != MAP_FAILED)
        for (size_t i = 0; i < pressure_mb * 1024 * 1024; i += 4096) pressure[i] = 1;
    sleep(3);
    if (pressure != MAP_FAILED) munmap(pressure, pressure_mb * 1024 * 1024);
}


static void demo_latency_gap(unsigned long *fast_ns, unsigned long *slow_ns) {
    struct timespec t0, t1;
    unsigned long fast[128], slow[64];
    int nf = 0, ns = 0;

    /* warm-up — force page-table walks to complete for both regions */
    for (int t = 0; t < 256; t++) {
        volatile char x = region[(t % HOT_PAGES) * PAGE_SIZE]; (void)x;
        volatile char y = region[(TOTAL_PAGES - 1 - t) * PAGE_SIZE]; (void)y;
    }

    /* fast tier: hot pages, guaranteed resident */
    for (int t = 0; t < 128; t++) {
        unsigned long idx = t % HOT_PAGES;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        volatile char x = region[idx * PAGE_SIZE]; (void)x;
        clock_gettime(CLOCK_MONOTONIC, &t1);
        fast[nf++] = (t1.tv_sec-t0.tv_sec)*1000000000UL + (t1.tv_nsec-t0.tv_nsec);
    }

    /* slow tier: pages we demoted and the kernel actually swapped out */
    for (unsigned long idx = TOTAL_PAGES / 2; idx < TOTAL_PAGES - 1 && ns < 64; idx += 512) {
        clock_gettime(CLOCK_MONOTONIC, &t0);
        volatile char x = region[idx * PAGE_SIZE]; (void)x;
        clock_gettime(CLOCK_MONOTONIC, &t1);
        unsigned long dt = (t1.tv_sec-t0.tv_sec)*1000000000UL + (t1.tv_nsec-t0.tv_nsec);
        if (dt > 1000) slow[ns++] = dt;  /* only count real slow reads */
    }

    /* median */
    for (int i = 0; i < nf; i++) for (int j = i+1; j < nf; j++)
        if (fast[j] < fast[i]) { unsigned long tmp = fast[i]; fast[i] = fast[j]; fast[j] = tmp; }
    for (int i = 0; i < ns; i++) for (int j = i+1; j < ns; j++)
        if (slow[j] < slow[i]) { unsigned long tmp = slow[i]; slow[i] = slow[j]; slow[j] = tmp; }

    *fast_ns = nf ? fast[nf/2] : 0;
    *slow_ns = ns ? slow[ns/2] : 0;
}


static void emit_json(unsigned long demoted) {
    unsigned long fast_ns = 0, slow_ns = 0;
    demo_latency_gap(&fast_ns, &slow_ns);
    unsigned char *vec = calloc(TOTAL_PAGES, 1);
    mincore(region, (size_t)TOTAL_MB * 1024 * 1024, vec);
    unsigned long resident = 0, swapped = 0;
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) if (vec[i] & 1) resident++; else swapped++;
    printf("{\n  \"demoted\": %lu,\n  \"resident\": %lu,\n  \"swapped\": %lu,\n  \"total\": %lu,\n"
           "  \"fast_ns\": %lu,\n  \"slow_ns\": %lu,\n  \"slowdown_x\": %.1f,\n  \"cells\": [",
           demoted, resident, swapped, (unsigned long)TOTAL_PAGES,
           fast_ns, slow_ns, fast_ns ? (double)slow_ns/fast_ns : 0.0);
    int cols = 64; int cell_pages = TOTAL_PAGES / (cols * 64); int first = 1;
    for (int r = 0; r < 64; r++) for (int c = 0; c < cols; c++) {
        unsigned long base = (unsigned long)(r * cols + c) * cell_pages;
        int res = 0;
        for (int k = 0; k < cell_pages; k++) if (vec[base + k] & 1) res++;
        if (!first) printf(",");
        printf("%d", (int)(100.0 * res / cell_pages));
        first = 0;
    }
    printf("]\n}\n");
    free(vec);
}

static void draw_live_view(const char *label, unsigned long demoted_so_far) {
    unsigned char *vec = calloc(TOTAL_PAGES, 1);
    if (!vec) return;
    if (mincore(region, (size_t)TOTAL_MB * 1024 * 1024, vec) != 0) { free(vec); return; }
    unsigned long resident = 0, swapped = 0;
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) if (vec[i] & 1) resident++; else swapped++;
    printf("\033[H\033[2J");
    printf("=== TIERED MEMORY LIVE — %s ===\n\n", label);
    printf("  Fast tier (RAM):  %7.1f MB   (%lu/%lu pages resident)\n",
           resident * PAGE_SIZE / 1024.0 / 1024.0, resident, (unsigned long)TOTAL_PAGES);
    printf("  Slow tier (swap): %7.1f MB   (%lu/%lu pages in swap)\n\n",
           swapped * PAGE_SIZE / 1024.0 / 1024.0, swapped, (unsigned long)TOTAL_PAGES);
    printf("  Queued for demotion: %lu\n\n  Page map:\n\n  ", demoted_so_far);
    int cols = 64; int cell_pages = TOTAL_PAGES / (cols * 64);
    for (int r = 0; r < 64; r++) {
        for (int c = 0; c < cols; c++) {
            unsigned long base = (unsigned long)(r * cols + c) * cell_pages;
            int res = 0;
            for (int k = 0; k < cell_pages; k++) if (vec[base + k] & 1) res++;
            double ratio = (double)res / cell_pages;
            if (ratio > 0.8)      fputs("\033[32m\u2588\033[0m", stdout);
            else if (ratio > 0.2) fputs("\033[33m\u2592\033[0m", stdout);
            else                  fputs("\033[31m\u2591\033[0m", stdout);
        }
        printf("\n  ");
    }
    printf("\n  Legend: fast  mixed  swap\n");
    fflush(stdout);
    free(vec);
}

static void policy_baseline_visual(void) {
    printf("\033[H\033[2J=== BASELINE ===\n\n  No policy applied.\n");
    sleep(2);
    draw_live_view("BASELINE", 0);
    sleep(3);
}

static void policy_adaptive_visual(void) {
    printf("\033[H\033[2J=== ADAPTIVE ===\n\n  Silent demote pass...\n");
    fflush(stdout);
    unsigned long d = run_demotion();
    printf("  Queued %lu pages.\n  Applying moderate pressure...\n", d);
    fflush(stdout);
    force_eviction();
    draw_live_view("ADAPTIVE", d);
    read_self_status("after policy");
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <baseline|adaptive> [--json]\n", argv[0]); return 2; }
    int adaptive = strcmp(argv[1], "adaptive") == 0;
    int json_mode = (argc >= 3 && strcmp(argv[2], "--json") == 0);
    srand(42);

    region = mmap(NULL, (size_t)TOTAL_MB * 1024 * 1024, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (region == MAP_FAILED) { perror("mmap"); return 1; }
    memset(meta, 0, sizeof(meta));

    if (!json_mode) fprintf(stderr, "[1] Allocated %d MB\n", TOTAL_MB);
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) region[i * PAGE_SIZE] = 1;
    workload_skewed(ACCESS_ROUNDS);

    if (json_mode) {
        unsigned long d = adaptive ? run_demotion() : 0;
        if (adaptive) force_eviction();
        emit_json(d);
    } else {
        if (adaptive) policy_adaptive_visual(); else policy_baseline_visual();
        printf("\n\033[?25h");
    }
    munmap(region, (size_t)TOTAL_MB * 1024 * 1024);
    return 0;
}
