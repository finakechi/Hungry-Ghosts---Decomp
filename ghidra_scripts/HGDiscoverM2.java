// Targeted discovery only in the separately copied M2 database.
// @category HungryGhosts
import ghidra.app.script.GhidraScript;
public class HGDiscoverM2 extends GhidraScript {
    public void run() throws Exception {
        if (!"a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61".equals(currentProgram.getExecutableSHA256()))
            throw new IllegalStateException("Wrong original executable");
        if(getFunctionAt(toAddr(0x130220))==null) {
            disassemble(toAddr(0x130220));
            createFunction(toAddr(0x130220),"hg_resource_worker_candidate");
        }
        if(getFunctionAt(toAddr(0x11df10))==null) {
            disassemble(toAddr(0x11df10));
            createFunction(toAddr(0x11df10),"hg_vblank_resource_wake_candidate");
        }
    }
}
