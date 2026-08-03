pragma circom 2.0.0;

// Normative profile: the 8-round PROGRAM_V2 of the spec
// (see program_v2_lib.circom for the templates + statement docs).

include "program_v2_lib.circom";

component main {public [st0]} = ProgramV2(8);
