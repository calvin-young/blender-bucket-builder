# What a packing benchmark would take

Status: a plan, with one small first data point. Nothing below tier 0 has been
run. The effort figures are estimates. Written 2026-11-05.

**In short.** Nobody has published what we would want: an independent test of
the commercial packers on the same parts under the same rules. Vendors publish
single before-and-after examples, and academics test against each other on
research data sets. A first useful result is about a week of work, mostly
access to the tools and hand-driving them. The packer isn't tuned yet, so the
first run should be about finding where it loses, not about a headline number.

## 1. First data point (tier 0, synthetic parts)

Bucket Builder 1.5.1 against Netfabb Premium 2021.1: 380 x 284 x 380 mm volume,
3 mm gap, 5 mm wall gap, no density limit. Both layouts were judged by the same
code (`python -m pack report`, `pack/referee.py`), not by each program's own
numbers. Part counts, volumes and triangle counts survived Netfabb's export.

| 147 mechanical parts | Inside | Height | Density | Peak layer | Time | Referee |
|---|---|---|---|---|---|---|
| Bucket Builder, free rotation | 147 | **34.0 mm** | **23.0 %** | 37.6 % | 62 s | clean |
| Netfabb Simple Outbox 3D (no rotation) | 147 | 77.0 mm | 10.1 % | 53.4 % | ~20 s | clean |
| Netfabb Simple Outbox, parts first turned as in our layout | 147 | 40.0 mm | 19.5 % | 41.8 % | ~15 s | clean |
| Bucket Builder, turning about z only | 147 | 76.5 mm | 10.2 % | 20.2 % | 14 s | clean |
| Bucket Builder, free, `--time 15` | 147 | 50.5 mm | 15.5 % | 33.1 % | 30 s | clean |
| Netfabb 2D Packing (one layer, Accurate, 20 degree steps) | 90 | 76.5 mm | | | ~40 s | 57 parts outside |

| 100 different chips | Inside | Height | Density | Time | Referee |
|---|---|---|---|---|---|
| Bucket Builder, free rotation | 100 | 230.1 mm | 8.1 % | 60 s | clean |
| Netfabb Simple Outbox 3D (no rotation) | 60 | 378.1 mm | 3.2 % (the 60) | ~90 s | 60 clean, 40 did not fit |

How to read this:

These chips ran on 1.5.1, as the table says. Nesting stacks (`pack/stack.py`, added the same day) took Bucket
Builder to 154.8 mm on them, and to 102 mm in two stacks side by side on the chips of the new reel.

* **It isn't a win over Netfabb's best.** Netfabb's 2021 help lists 3D Scanline,
  Monte Carlo, Gravity and Size Sorting. The menus of the installation we used
  offered only Simple Outbox Packing and 2D Packing, so those four have not been
  run. Simple Outbox is the program's fast box packer and doesn't rotate parts.
* **Most of the 34 vs 77 mm is rotation.** Every part stays upright in the
  77 mm layout, and the tallest is a 76.5 mm bolt. Upright, the two programs tie
  on height (76.5 vs 77.0 mm), and what's left is even layers (peak 20 % vs 53 %).
  Given our orientations, the simple packer reaches 40 mm in seconds, against
  our 34 mm in a minute. Our short run (50.5 mm) did worse than that.
* **This bucket flatters orientation and the chips flatter nesting.** Both were
  made for our demos. Neither is a neutral test.
* **23 % isn't a build to print.** The published range for MJF is 8 - 12 %
  ([PRINT_QUALITY.md](PRINT_QUALITY.md)). It measures packing with the limit off.
* Times come from different machines, and Netfabb's are approximate.
* Netfabb's 2D packer has no numeric gap: "Average" gave a closest pair of 1.22 mm.

## 2. What already exists

**Independent or academic.**

* Araújo et al. 2015, [classification and benchmarks](https://eprints.nottingham.ac.uk/33945/):
  says packing methods are usually tested on shapes unlike real products.
* Araújo et al. 2019, [taxonomy and dataset](https://doi.org/10.1080/00207543.2018.1534016)
  (IJPR): a data set meant for this problem, the closest thing to a standard.
  [Accepted manuscript](https://nottingham-repository.worktribe.com/output/1190642).
  We couldn't open the manuscript, so where the files are hosted is unconfirmed.
* Lamas-Fernandez et al. 2023, [voxel-based 3D irregular packing](https://doi.org/10.1287/opre.2022.2260)
  (Operations Research, open access): lowest height, new instances including
  AM models. [Code on GitHub, MIT license](https://github.com/c-lamas/voxel_packing).
  It uses the Stoyan instances from the ESICUP cutting-and-packing group.
* Cui, Rong, Chen, Matusik 2023, [spectral packing](https://dspace.mit.edu/handle/1721.1/152166)
  (MIT and Inkbit): voxels and FFT, as ours is; interlocking-free; over 6,000
  Thingi10K objects at 35.77 % in its summary, and
  [6,596 objects in two hours at 37.30 %, 670 in 40 s at about 36 %](https://news.mit.edu/2023/chore-packing-just-got-faster-and-easier-0706).
  No public code found. The best evidence that very large scale works, but
  the authors built the method and the rules differ from MJF's.
* Yau and Hsu 2022, [3D nesting for powder processes](https://doi.org/10.1007/s00170-021-07954-y)
  (IJAMT): build height about 12 - 13 % better than Netfabb and Magics in about
  160 s. Their own method, and the only named comparison we found. We read only
  an excerpt, not the full paper.
* RWTH ACAM, [AM Build Job Nesting Optimizer](https://acam.rwth-campus.com/wp-content/uploads/sites/11/2014/10/ACAM-Demonstratoren-DAP_Teil6.pdf)
  (one-page demonstrator sheet): "Prusa Mini x100", 25 models, 6,100 instances,
  30.21 % after 49 s and 31.70 % after 27.5 min. Includes a disassembly check
  against interlocking. Not a comparison, and the test set isn't published there.

**Vendor or reseller claims. None gives the parts or settings to repeat them.**

* CoreTechnologie 4D_Additive 1.6: [one HP MJF 4200 job](https://www.additivemanufacturing.media/products/coretechnologie-optimizes-ai-supported-3d-nesting-software-for-higher-efficiency),
  357 to 283 mm, density 8 to just over 10 %, 8 to 3.5 minutes, "30 % efficiency"
  ([repeated from press material](https://digitalengineering247.com/article/coretechnologie-releases-4d-additive-v1.6)).
* AMIS: [1,500 parts in under a minute at 14 % against 8 - 9 %](https://www.3dprintingjournal.com/p/is-autodesk-fusions-new-nesting-tool),
  stated by its managing director, against unnamed "standard solutions".
* Hawkridge, an HP and Materialise reseller:
  [geometry nesting in Magics fits almost twice the parts of HP's bounding-box default](https://hawkridgesys.com/blog/how-to-maximize-nesting-efficiency-in-your-3d-printing-builds),
  one sample build, about 5.6 % for the box method. Seen in search excerpts only;
  the page didn't load for us.
* Autodesk: [True Shape described as its best method](https://www.autodesk.com/products/fusion-360/blog/3d-nesting-autodesk-fusion/),
  without measured density. Seen in search excerpts only.

## 3. What to compare

| Program | Methods, by its own documentation | Priority | Status |
|---|---|---|---|
| [Netfabb](https://help.autodesk.com/cloudhelp/2021/ENU/NETF/files/GUID-52326727-C6CE-44DA-ACC3-7BBBFF7694DC.htm) | Simple, 2D, 3D Scanline, Monte Carlo, Gravity, Size Sorting; [2026 adds True Shape](https://help.autodesk.com/cloudhelp/2026/ENU/NETF/files/GUID-52326727-C6CE-44DA-ACC3-7BBBFF7694DC.htm) | 1 | Simple and 2D run; 3D packers need access |
| [Magics Sinter Module](https://www.materialise.com/en/inspiration/articles/optimized-nesting-how-to) | Bounding box and geometry nesters | 1 | Needs access (the industry reference) |
| HP SmartStream 3D Build Manager | Auto-pack | 1 | Installed and seen, not yet run |
| [Autodesk Fusion](https://help.autodesk.com/cloudhelp/ENU/Fusion-CAM/files/MFG-AM-HOW-TO-ARRANGE.htm) | 2D and 3D True Shape; [bounding box](https://help.autodesk.com/cloudhelp/ENU/Fusion-CAM/files/MFG-AM-HOW-TO-FILL-BUILD-VOLUME.htm); Monte Carlo in older posts | 1 | Needs access |
| [4D_Additive](https://www.dynagraph.net/4d-additive), AMIS | Automatic 3D nesting | 2 | Trial or vendor run |
| [voxel_packing](https://github.com/c-lamas/voxel_packing) | Academic, open code | 2 | The one reference method anyone can rerun |
| [DELMIA Powder Bed Programmer](https://3dswym.3dexperience.3ds.com/post/delmia-fabrication/additive-manufacturing-3d-nesting-auto-manual-combo_sGndNXSJSJuI5jrwrYu_eQ), others | Not reviewed | 3 | Only if someone asks |

Open source is thin. [3DNester-cpp](https://github.com/ddm-j/3DNester-cpp) packs one
model and has had no commits since 2022. [irregular-object-packing](https://github.com/gzavo/irregular-object-packing)
handles about ten items. Neither is a fair opponent. No search covered every
product, so this table isn't a full market survey.

## 4. What to pack

No standard bucket exists for commercial tools, so we would make one and publish it.

* **Ours, ready now:** the synthetic library in `pack/synthetic.py` (AUTOPACK §8.4).
  Starter set of six, chosen to pull apart different abilities: `bureau-day` (mixed),
  `varied-chips` (nesting), `huge-and-tiny` (scale range), `nuts` (many small
  parts), `tees` (interlocking risk), `mix-a` (random).
* **Other people's, for credibility:** the Araújo set if its files turn up, the
  Stoyan instances, and a sample of Thingi10K. Its
  [licenses are per model](https://pypi.org/project/thingi10k/), and the package
  can filter to Creative Commons, so publish only the ones that allow it.
* **To create:** buckets that look like real MJF work, with realistic mixes and
  triangle counts, since ours were made to test the packer rather than judge it.
* **Private:** real buckets stay on local machines (`benchmarks/private/`). They
  can guide our work; published results stay synthetic.

## 5. Rules that make it fair

1. **One judge.** Export every layout to 3MF and measure it with the same code.
   Check part count, volume and triangles against the input. Any collision, gap or
   wall violation, or part outside, disqualifies the layout or is reported beside it.
2. **Same inputs and limits:** volume, gap, wall gap. A tool without a numeric gap
   gets its nearest setting above the limit, and the measured gap is reported.
3. **Declare rotation.** Run free, z-only and fixed separately. Today's table shows
   this choice can matter more than the packer. Where a tool can't rotate, say so
   rather than compare across modes.
4. **Time limits:** wall clock at about 30 s, 5 min and 60 min, on one machine, with
   threads and GPU recorded. Report time to first layout as well.
5. **Two tasks:** a fixed list at the lowest height, and fill the volume with as
   many parts as possible. Tools may be strong at one and weak at the other.
6. **Two density settings:** no limit, to measure packing power, and the 12 % limit,
   to measure what could be printed.
7. **One density definition:** part volume over build area times filled height,
   lowest point to highest. [SmartStream's guide](https://manualmachine.com/hp/smartstream3dbuildmanager/2938361-users-guide)
   measures up to the highest part, the same unless a build is raised off the
   floor. Every vendor number above should be read with this question in mind.
8. **More than density:** peak layer and layer spread, as uneven layers cost part
   quality.
9. **Interlocking must be checked by us.** A dense layout that traps parts is
   worthless. Our referee doesn't check for it yet (the design is AUTOPACK §8.9),
   so tier 1 needs that check, or at least Netfabb's own Interlocking Test as a
   second opinion.
10. **Repeats:** at least three seeds or runs for randomized methods; report the
    median and the best.
11. **Fair settings:** each tool gets its documented best settings, written down,
    and ideally a look from someone who uses it daily. Publish inputs, settings and
    output layouts so anyone can check.

## 6. Effort (estimates)

| Tier | What | Effort | Gives |
|---|---|---|---|
| 0 | One bucket, Netfabb's simple packers | Done | Pipeline proven: export, judge, images |
| 1 | Six synthetic buckets, four priority-1 programs, 3D packers included, free and z-only, one time limit, interlocking check | 1 - 2 weeks, plus access to the tools | First honest ranking, and where we lose |
| 2 | About 20 buckets with public sets, three time limits, repeats, tier 2 programs, published repo | 3 - 6 weeks | Citable public benchmark |
| 3 | Print the top layouts for a few buckets: scrap, accuracy, cooling, powder | Months, with machine time and a lab | Shows whether density gains survive printing |

Most of the time in tier 1 is hand-driving GUIs: roughly 20 minutes per program per
bucket and setting. Netfabb has Lua scripting and Magics may have automation, which
would pay for itself by tier 2. Tier 3 is the one that answers what a customer asks,
and we found nothing published that does it.

## 7. Recommended first step

Run Netfabb's 3D Scanline and Monte Carlo and SmartStream's Auto-pack on the
six-bucket starter set with the rules above, plus the interlocking check. That is
under a week once Netfabb's 3D packers are available, and SmartStream is already at
hand. Then tune the packer against what it shows, and only then add Magics and
Fusion and publish. Until the 3D packers have been run, today's result should be
described as a comparison with Netfabb's simple packer.

## 8. Requested / assumed

* **Requested:** one simple side by side with a Netfabb algorithm; a short report on
  software, algorithms, buckets, metrics, effort and a first step, with evidence
  separated from claims.
* **Assumed:** the extra runs in section 1 (same orientations, z-only, short time)
  as a check on the headline; voxel size 1 mm for Simple Outbox, where the default
  is 5 mm; the tiers, effort figures, priorities and starter set.
* **Not asked:** whether results will be published with named products, and on what
  terms.
* **Follow-ups:** access to Netfabb's 3D packers, Magics and Fusion; the interlocking
  check; the location of the Araújo data set.

## 9. Access and terms (checked 2026-11-05; not legal advice)

* **Netfabb.** Run without a license, any edition falls back to the free
  feature set; our 2021.1 install says "not licensed" and offers only Simple
  Outbox and 2D packing. No Autodesk-hosted 2019 installer was found, and
  third-party copies were not used. The current Premium has a 30-day trial
  through an Autodesk account. Autodesk's General Terms (§9) limit trials to
  30 days of non-commercial evaluation by one person, and forbid reverse
  engineering, including studying an offering's algorithms or internals. No
  benchmarking clause was found in them.
* **Fusion.** 30-day trial; Arrange is not in the personal edition.
* **Magics.** Evaluation licenses go through Materialise or a reseller. The
  2025 EULA (§5.3) limits them to 30 days, to test whether the software suits
  the customer's own needs, and forbids reverse engineering (§7.5). No
  benchmarking or publication clause was found.
* **SmartStream 3D Build Manager.** Offers no printer without a Command Center
  connection (its default volume is 248 mm tall), and its auto-pack spacing is
  5 mm at least. Its license text has not been read yet.
* For this project: compare layouts we measure, never describe how another
  program works inside (AGENTS.md, Conventions).
