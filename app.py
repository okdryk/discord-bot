#!/usr/bin/env python3
import aws_cdk as cdk

from stacks.palworld_stack import PalworldStack

app = cdk.App()
PalworldStack(app, "PalworldBotStack")
app.synth()
