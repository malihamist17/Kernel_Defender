/*
 * defender_module.c — Kernel Defender's Loadable Kernel Module (LKM).
 *
 * WHAT THIS DOES (and nothing more):
 *   - Registers a /proc/kernel_defender entry.
 *   - Reading it returns a small status block (mode, monitoring flag,
 *     incident count, last event) held in kernel memory.
 *   - Writing a line to it updates that state using a tiny text protocol:
 *       MODE:<NORMAL|MONITOR|PROTECT>   -> changes the mode
 *       EVENT:<free text>               -> records an event + increments
 *                                          the incident counter
 *
 * WHAT THIS DELIBERATELY DOES NOT DO (by design, for safety):
 *   - No CPU frequency/voltage changes, no overclocking.
 *   - No killing or signalling of arbitrary processes.
 *   - No manipulation of other kernel subsystems, locks, or memory.
 *   - No scheduling changes.
 * All of the actual "safety response" (renice, governor changes, reaping,
 * etc.) still happens in user-space, in Kernel Defender's existing Python
 * code — this module is ONLY a demonstration of real kernel-space state
 * plus a real kernel/user-space communication channel via /proc.
 *
 * BUILD:   cd backend/kernel && make
 * LOAD:    sudo insmod defender_module.ko
 * VERIFY:  cat /proc/kernel_defender
 * WRITE:   echo "MODE:PROTECT" | sudo tee /proc/kernel_defender
 * UNLOAD:  sudo rmmod defender_module
 *
 * You must build this on YOUR machine against YOUR running kernel's
 * headers (`sudo apt install linux-headers-$(uname -r)`). A .ko built
 * elsewhere will not load — kernel modules are tied to the exact kernel
 * version/config they were compiled against.
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/proc_fs.h>
#include <linux/uaccess.h>
#include <linux/version.h>
#include <linux/mutex.h>

#define PROC_NAME "kernel_defender"
#define MAX_EVENT_LEN 128
#define MAX_MODE_LEN 16

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Kernel Defender Project");
MODULE_DESCRIPTION("Kernel Defender status/mode module - /proc/kernel_defender");
MODULE_VERSION("1.0");

static DEFINE_MUTEX(defender_lock);
static char current_mode[MAX_MODE_LEN] = "NORMAL";
static char last_event[MAX_EVENT_LEN] = "Module loaded";
static unsigned long incident_count = 0;
static bool monitoring_enabled = true;

static struct proc_dir_entry *defender_proc_entry;

static ssize_t defender_read(struct file *file, char __user *ubuf,
                              size_t count, loff_t *ppos)
{
    char buf[512];
    int len;

    if (*ppos > 0)
        return 0; /* single-shot read, like most simple /proc files */

    mutex_lock(&defender_lock);
    len = snprintf(buf, sizeof(buf),
        "Kernel Defender\n"
        "Mode: %s\n"
        "Monitoring: %s\n"
        "Incidents: %lu\n"
        "Last Event: %s\n",
        current_mode,
        monitoring_enabled ? "ENABLED" : "DISABLED",
        incident_count,
        last_event);
    mutex_unlock(&defender_lock);

    if (copy_to_user(ubuf, buf, len))
        return -EFAULT;

    *ppos = len;
    return len;
}

static ssize_t defender_write(struct file *file, const char __user *ubuf,
                               size_t count, loff_t *ppos)
{
    char buf[160];
    size_t n = min(count, sizeof(buf) - 1);

    if (copy_from_user(buf, ubuf, n))
        return -EFAULT;
    buf[n] = '\0';

    /* strip trailing newline, if any (echo adds one) */
    if (n > 0 && buf[n - 1] == '\n')
        buf[n - 1] = '\0';

    mutex_lock(&defender_lock);

    if (strncmp(buf, "MODE:", 5) == 0) {
        const char *requested = buf + 5;
        if (strcmp(requested, "NORMAL") == 0 ||
            strcmp(requested, "MONITOR") == 0 ||
            strcmp(requested, "PROTECT") == 0) {
            strscpy(current_mode, requested, sizeof(current_mode));
            monitoring_enabled = strcmp(requested, "NORMAL") != 0;
            printk(KERN_INFO "kernel_defender: mode changed to %s\n", current_mode);
        } else {
            printk(KERN_WARNING "kernel_defender: rejected unknown mode '%s'\n", requested);
        }
    } else if (strncmp(buf, "EVENT:", 6) == 0) {
        strscpy(last_event, buf + 6, sizeof(last_event));
        incident_count++;
        printk(KERN_INFO "kernel_defender: event recorded: %s\n", last_event);
    } else {
        printk(KERN_WARNING "kernel_defender: unrecognized command '%s'\n", buf);
    }

    mutex_unlock(&defender_lock);
    return count;
}

#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 6, 0)
static const struct proc_ops defender_fops = {
    .proc_read  = defender_read,
    .proc_write = defender_write,
};
#else
static const struct file_operations defender_fops = {
    .owner = THIS_MODULE,
    .read  = defender_read,
    .write = defender_write,
};
#endif

static int __init defender_init(void)
{
    /* 0666: readable/writable by any local user - deliberate, so this
     * coursework demo doesn't require running the whole Python backend
     * as root just to flip modes. Fine for a benign status/counter
     * module; would NOT be appropriate for a module that controls
     * anything privileged. */
    defender_proc_entry = proc_create(PROC_NAME, 0666, NULL, &defender_fops);
    if (!defender_proc_entry) {
        printk(KERN_ERR "kernel_defender: failed to create /proc/%s\n", PROC_NAME);
        return -ENOMEM;
    }
    printk(KERN_INFO "kernel_defender: module loaded, /proc/%s ready\n", PROC_NAME);
    return 0;
}

static void __exit defender_exit(void)
{
    proc_remove(defender_proc_entry);
    printk(KERN_INFO "kernel_defender: module unloaded\n");
}

module_init(defender_init);
module_exit(defender_exit);
