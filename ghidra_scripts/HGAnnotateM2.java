// Analyst labels in the separately copied M2 database; original ABI unchanged.
// @category HungryGhosts
import ghidra.app.script.GhidraScript;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.listing.Function;
public class HGAnnotateM2 extends GhidraScript {
    public void run() throws Exception {
        if(!"a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61".equals(currentProgram.getExecutableSHA256()))
            throw new IllegalStateException("Wrong executable");
        long[] entries={0x1431f0,0x143360,0x1434f0,0x130150,0x130170,0x130220,0x1303e0,0x1306a0,0x12fbc0,0x131810,0x131dd0};
        String[] names={"hg_resource_init","hg_resource_schedule","hg_resource_finish","hg_resource_request_raw","hg_resource_request","hg_resource_worker","hg_pack_special_step","hg_pack_bootstrap","hg_area_index","hg_area_read_step","hg_area_finalize"};
        for(int i=0;i<entries.length;i++) {
            Function f=getFunctionAt(toAddr(entries[i]));
            if(f==null)throw new IllegalStateException("Missing function "+names[i]);
            f.setName(names[i],SourceType.USER_DEFINED);
            f.setComment("HG M2 analyst reconstruction; original-byte/model differential checked. See analysis/resource_loader/functions.json and explicit dependencies in src/resource_loader.c. Host context parameters are not original ABI.");
        }
        createLabel(toAddr(0x3f8e00),"hg_resource_thread_descriptor",true);
        createLabel(toAddr(0x3f8e30),"hg_resource_thread_active",true);
        createLabel(toAddr(0x3f3b00),"hg_pack_directory_buffer",true);
        createLabel(toAddr(0x3f4300),"hg_resource_handles",true);
    }
}
