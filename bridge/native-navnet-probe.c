#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>

typedef int16_t (*init_fn)(const char *);
typedef int16_t (*start_fn)(uint32_t, void *, void (*)(void *, void *));
typedef int16_t (*stop_fn)(uint32_t);
typedef int16_t (*ss_init_fn)(void);

int main(void) {
    const char *path = "/Applications/TI-Nspire CX CAS Student Software.app/Contents/Java/libnavnet.dylib";
    void *lib = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!lib) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 2; }
    init_fn init = (init_fn)dlsym(lib, "TI_NN_Init");
    start_fn start = (start_fn)dlsym(lib, "TI_NN_SS_StartService");
    stop_fn stop = (stop_fn)dlsym(lib, "TI_NN_SS_StopService");
    ss_init_fn ss_init = (ss_init_fn)dlsym(lib, "TI_NN_SS_Init");
    if (!init || !start || !stop) { fprintf(stderr, "missing native symbols\n"); return 3; }
    int16_t a = init("-c 0 -d 0");
    int16_t s = ss_init ? ss_init() : -999;
    int16_t b = start(0x5001, NULL, NULL);
    printf("init=%d ss_init=%d start=0x%04x\n", a, s, (unsigned short)b);
    if (b >= 0) printf("registered\n");
    if (b >= 0) printf("stop=%d\n", stop(0x5001));
    return b < 0;
}
