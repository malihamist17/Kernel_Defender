#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdint.h>
#include <fcntl.h>
#include <sys/mman.h>

#define PAGE_SIZE     4096
#define TOTAL_MB      64
#define TOTAL_PAGES   ((TOTAL_MB * 1024 * 1024) / PAGE_SIZE)
#define HOT_PAGES     256

typedef struct { unsigned long count; unsigned long last; } meta_t;
static meta_t meta[TOTAL_PAGES];
static char *region = NULL;
static int pm_fd = -1;

static uint64_t pm_entry(void *va) {
    off_t off = ((uintptr_t)va / PAGE_SIZE) * 8;
    uint64_t e = 0;
    if (pread(pm_fd, &e, 8, off) != 8) return 0;
    return e;
}

static void decompose(uintptr_t va, int *pgd, int *pud, int *pmd, int *pte, int *off) {
    *off = va & 0xFFF;
    *pte = (va >> 12) & 0x1FF;
    *pmd = (va >> 21) & 0x1FF;
    *pud = (va >> 30) & 0x1FF;
    *pgd = (va >> 39) & 0x1FF;
}

static void run_workload(void) {
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) region[i * PAGE_SIZE] = 1;
    for (unsigned long r = 0; r < 200000; r++) {
        unsigned long idx = (rand() % 100 < 80) ? (rand() % HOT_PAGES) : (rand() % TOTAL_PAGES);
        region[idx * PAGE_SIZE] = 1;
        meta[idx].count++; meta[idx].last = r;
    }
}

static unsigned long run_demotion(void) {
    unsigned long demoted = 0, run_start = 0; int in_run = 0;
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) {
        int hot = (meta[i].count >= 50) && (199999 - meta[i].last < 1000);
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
    return demoted;
}

static void emit_json(unsigned long demoted) {
    unsigned long present = 0, swapped = 0, absent = 0;
    for (unsigned long i = 0; i < TOTAL_PAGES; i++) {
        uint64_t e = pm_entry(region + i * PAGE_SIZE);
        int p = (e >> 63) & 1, s = (e >> 62) & 1;
        if (p) present++; else if (s) swapped++; else absent++;
    }
    char status[129];
    for (int i = 0; i < 128; i++) {
        uint64_t e = pm_entry(region + i * PAGE_SIZE);
        int p = (e >> 63) & 1, s = (e >> 62) & 1;
        status[i] = p ? 'P' : (s ? 'S' : '.');
    }
    status[128] = 0;
    printf("{\n  \"region_base\": \"%p\",\n  \"total_pages\": %lu,\n  \"demoted\": %lu,\n",
           (void*)region, (unsigned long)TOTAL_PAGES, demoted);
    printf("  \"present\": %lu,\n  \"swapped\": %lu,\n  \"absent\": %lu,\n",
           present, swapped, absent);
    printf("  \"status_prefix\": \"%s\",\n", status);
    printf("  \"decomposition\": [");
    int samples[] = {0, 512, 1024, 2048, 4096, 8192};
    for (int si = 0; si < 6; si++) {
        int i = samples[si];
        uintptr_t va = (uintptr_t)(region + i * PAGE_SIZE);
        int pgd, pud, pmd, pte, off;
        decompose(va, &pgd, &pud, &pmd, &pte, &off);
        unsigned long pn = va >> 12;
        printf("%s{\"page\":%d,\"addr\":\"0x%012lx\",\"pageno\":\"0x%lx\","
               "\"offset\":\"0x%03x\",\"pgd\":\"0x%03x\",\"pud\":\"0x%03x\","
               "\"pmd\":\"0x%03x\",\"pte\":\"0x%03x\"}",
               si ? "," : "", i, (unsigned long)va, pn, off, pgd, pud, pmd, pte);
    }
    printf("]\n}\n");
}

int main(int argc, char **argv) {
    int json_mode = (argc >= 2 && strcmp(argv[1], "--json") == 0);
    srand(42);

    region = mmap(NULL, (size_t)TOTAL_MB * 1024 * 1024, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (region == MAP_FAILED) { perror("mmap"); return 1; }
    memset(meta, 0, sizeof(meta));
    pm_fd = open("/proc/self/pagemap", O_RDONLY);
    if (pm_fd < 0) { perror("open pagemap"); return 1; }

    run_workload();

    if (json_mode) {
        unsigned long d = run_demotion();
        sleep(5);
        emit_json(d);
    } else {
        printf("Region base: %p\n  Hot set: pages 0..%d\n\n", (void*)region, HOT_PAGES - 1);
        unsigned long d = run_demotion();
        printf("  queued %lu pages for demotion, waiting 5s...\n", d);
        sleep(5);
        unsigned long p = 0, s = 0, a = 0;
        for (unsigned long i = 0; i < TOTAL_PAGES; i++) {
            uint64_t e = pm_entry(region + i * PAGE_SIZE);
            if ((e >> 63) & 1) p++; else if ((e >> 62) & 1) s++; else a++;
        }
        printf("\n  [after] present=%lu  swapped=%lu  absent=%lu\n\n", p, s, a);
        printf("  page   virtual address       page number     offset    PGD  PUD  PMD  PTE\n");
        printf("  ----   -------------------   -------------   -------   ---  ---  ---  ---\n");
        for (int i = 0; i < 6; i++) {
            uintptr_t va = (uintptr_t)(region + i * PAGE_SIZE);
            int pgd, pud, pmd, pte, off;
            decompose(va, &pgd, &pud, &pmd, &pte, &off);
            unsigned long pn = va >> 12;
            printf("  %4d   0x%012lx   0x%08lx      0x%03x    %3d  %3d  %3d  %3d\n",
                   i, (unsigned long)va, pn, off, pgd, pud, pmd, pte);
        }
    }
    close(pm_fd);
    munmap(region, (size_t)TOTAL_MB * 1024 * 1024);
    return 0;
}
