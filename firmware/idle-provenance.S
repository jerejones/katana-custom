# Stock idle-countdown block only: e35c..e3ec. Origin of suppression is
# this exact auto-blank path, never an inferred off-state snapshot.
# Preserve every original countdown exclusion. Only an already-enabled,
# normal-mode display with a present V2 background suppresses idle blank.
# No ownership grant, interrupt masking, or GPIO added.
#
# Provenance record: when THIS path blanks the display it sets the word at
# a000ffa8 to 1. Every pass that finds the display on clears it again, so an
# explicit stock display-off (which never comes through here) can never leave
# it set. background_attempt in endpoint.S wakes the display on SET only while
# this record is 1; it is the only consumer.
# The record lives at a000ffa8 = gp+32648; gp addressing survives the stock
# render calls below, which clobber t0.
idle_entry:
 lw v1,-32720(gp)
 beq v1,zero,idle_count
 lbu v0,-32223(gp)
 sw zero,32648(gp) # display is on: any idle-blank record is stale
idle_count:
 beq v0,zero,idle_exit
 lui t0,0xa001
 lbu v1,-30224(t0)
 addiu v0,zero,3
 beq v1,v0,idle_exit
 lw v0,-32152(gp)
 bne v0,zero,idle_exit
 lbu v0,-32223(gp)
 addiu v0,v0,-1
 sb v0,-32223(gp)
 bne v0,zero,idle_exit
 lbu v0,-30154(t0)
 andi v0,v0,1
 bne v0,zero,idle_exit
 lw v0,-96(t0)
 addiu v0,v0,-1
 bne v0,zero,idle_blank
 addiu v1,v1,-1
 bne v1,zero,idle_blank
 lw v0,-32720(gp)
idle_decision:
 bne v0,zero,idle_exit
 nop
idle_blank:
 jal 0x9d02ac5c
 addiu a0,zero,50
 lui a0,0x9d04
 addiu a0,a0,-2724
 addiu a1,zero,8
 jal 0x9d0374a4
 move a2,zero
 sw zero,-32720(gp)
 addiu v0,zero,1
 sw v0,32648(gp) # this blank came from the stock idle timeout
idle_exit:
table:
