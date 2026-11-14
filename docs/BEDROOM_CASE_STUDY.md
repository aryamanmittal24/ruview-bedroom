# Bedroom sensing: integrating hardware without overstating it

## Goal and ownership

Connect ESP32 Wi-Fi measurements and existing WiZ bulbs to a local 3D room dashboard. Aryaman selected and connected the hardware, provided reference photos and approximate dimensions, tested movement and guided feature changes. Source implementation and troubleshooting used AI assistance. RuView itself is an upstream project, not an original invention of this repository's owner.

## Development milestones

1. **Find and run RuView:** identify the upstream firmware and host requirements, then run a local dashboard.
2. **Bring up the first ESP32:** inspect the chip, flash matching firmware, provision Wi-Fi and verify packets arriving at the Mac.
3. **Model the bedroom:** refine a room from photos into an approximately 11 × 10 ft scene shared with the WiZ dashboard design.
4. **Debug activity display:** investigate live packets with a static map, a saturated motion value and a marker hidden behind furniture.
5. **Correct the presentation:** remove the fixed floor blip because it implied a measured position. Show room-wide activity and explicit uncertainty instead.
6. **Integrate WiZ:** register for local pushes, expose heartbeats separately from occupancy events and investigate SpaceSense calibration and placement.
7. **Persist calibration:** retain baselines across refreshes, expire stale events and capture an outside-bedroom test while adjacent-room movement was possible.
8. **Add a second board:** back up its original flash, verify ESP32-S3/16 MB/8 MB hardware, provision Node 2 and verify CSI data. Extend the local movement workflow for two node IDs.

## Lessons from testing

An online sensor is not proof of reliable occupancy detection. WiZ heartbeat traffic is not a motion event. A room model does not give CSI a coordinate system. Adjacent-room movement can influence measurements across a wall. A second receiver increases available observations but positioning still requires an appropriate measurement model and validation.

## Evidence and outstanding work

Transport and firmware bring-up were observed on physical boards. Five movement-learning software tests passed at publication. Robust bedroom-vs-bathroom discrimination, human-vs-cat separation and medical readings remain unvalidated. WiZ occupancy remains conditional on working SpaceSense events from the phone-configured system.

## How to read the history

The first GitHub commit is a source snapshot uploaded on 15 November 2026. Earlier local changes were not committed individually. The sequence below is reconstructed from the development conversation and retained code; it is not a claim that Git recorded those stages. New commits preserve actual changes from publication onward. No contribution dates have been backfilled.

[Browse commits and diffs](https://github.com/aryamanmittal24/ruview-bedroom/commits/main/)
