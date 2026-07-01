#!/usr/bin/env python3
"""
Bit terminal replica v3.

This version is generated from the approved Bit image and embedded as a
high-fidelity 160x160 RGB terminal sprite. It prints as 160 columns x 80 rows
using ANSI truecolor plus Unicode half blocks.

No PNG is required at runtime.
No external dependencies are required.
"""

from __future__ import annotations

import argparse
import base64
import os
import shutil
import sys
import zlib
from typing import Iterable

SOURCE_WIDTH = 160
SOURCE_HEIGHT = 160
DEFAULT_COLS = 160
RESET = "\033[0m"

DATA_B64 = """
eNrtnQd4FNXax2dmd/puSO891ECy2fRCSUjvDQIIWBBUmiAqIlUEVBQsKCqC9BQIEEICVhAChJLeICpFxXsVQa+f
elUSkvnOOTOzuwkJEq/ea8y8z/85z9nZszOz8zvve8qcncUwxRRTTDHFFFNMMcUUU0wxxRRTTDHFFFNMMcUUU0wx
xRRTTDHFFFNMMcUUU0wxxRRTTDHFFFNMMcUUU+xWwzHcRH/pMxXPsDecqmKK/bm+QOAqNdCAJwqHrq0cuvq0z+py
v/WVTtkLcEJNkDQo8BdyWwxTsdrBS98f+nyF97OnvZ87M2z1GcbeS/wiCszbmO/rF4LfF/TFwqA9wrDDgt2UV/+K
YRnw1Vj45/4cVCoE7BcCi4XgEoFz94XvEgrfLi4X6zjQetQEy7Bs3ZuXdHtvhu25Me3IjRnlNyNXHeoXMcEubgrv
FfBX8Q50wmqNud/b3/jvvqnPb9EXtAYU3uTchil8u7haKjVIHdLmRpQJgSVC4D5hUEH7+Pfb2gRo2z4V+u8RRp4U
3Ka8aCj8l+DLm+s2fa3f1eqXdwMgDtjdyrma8MVxnFCZqo/ztUt4KPjgTb/8XwL2tA0uaB/3PsDbDvhuP9/mtuWX
sHdanSeu+kvxVWks/XfdDH5XCDooBB0SQt4VeA+d4r/d8bVPnAEukd+uFv2eNp/C9vCi9jnH2x453pZ5sM17Z0vw
IcF50nOwD6am/sfjNShIECcZm/gZdqmP2CbPsU2ZCzKkmTVqQKCr8p76AU/s8pyb6/VIbv/HClwmr8bwvs43WOLb
DuS7u90rr90rt917l+Bf2AbechL9txeMAyBfy5CM8GNC8EGo0MOC7q0v8L7N1w7wPdSuL7ihL2yH2iME7GkP3Neu
29ky6NXvh23/xWH8q4TWQ2U+CFez/83TI2iONLcl+8mCeRsxRIsDOoOkjYiveUByUEmLPv8X/4Jfg4pahr1Uh/V1
vrOC3xH0u9r1uwWEWPAraA8oEjyfu86k1vE5TZrsek1WrXbMObVNAPgMRqj/OydmG/dQ4O4fdG9/67f1e78t/9Lv
/D/f9RfB4NfQEMsxnBAl8Q1MDXlH8Ae1dK8QXCr4vNrcx/naxD7gv/tH343X9HmtkPKudr+89oA9Qv/V19jMWs34
Rj67jgd8x55XW3r/Vyte0uwQVPEgrEIBVDm/zddVnFlHvqbxGbbO/fwSAgp/9Nv2L/2OfwXs+tF79Rmsb89fgjCo
7merNrP2XtsIBh26bTd8t4L41uq++FNcv0MdksfEHgb+y2dUMyGvUD6L6WFP4FQ/6Mh/2ojYGFhK2+EgaFebf8FN
/73tuo1fG/mio6vMB2pGbeRHvaEJfwEnNaj3RZPmdlI8N7dTa62UjrRoPq9+BgIa8BT/XULQAcHr6a9wXZ46eA8T
+5FmTD2fXsUlV3JJVXz8MVzN/TfiM+B7UNDntcCoUtDmv1fQbbqq5vqZ8lXbhvSb+A+ziZ+Z59QTtKUC8TbDEPPA
NOtR91iNmGwVcbf1yHvMfDIIi+Eq6xGUbgUbX8ZEHWJjPmDjjnIx75DuE0iXbJVloNT8/Ul8E2YGHWj12/kzQOyX
C7y41XfDP9V8J76h5hO/MptwyXxMncz3llGVYrc1tccUJvo4FVHMxB3jEk+wccfYuDIuuY4etvjPHbilPQ5GOoHF
aB6jRICdwLxWtcaiQ3zWevBBz7D+yzjdAjmqKEC7HT/C4QaBBDOghSXBVtIlhwnbzgSu50YfAnCZ6A+YyINs7DHa
ZwnO2BCcwx/eoxZ7StohI9zuf8l54vPOk9e6TFrjMvlFp5ynCYrtrn+l2O+fFsTVOE5QPiuo4aVkcB4z+n029ggb
/R4XVcpFlhC8Oxo3/U9menHU00NS7D8zWvcCHXUUBGo25jAbf4yN/QiIi/4Qp63/pIDSaR7jLzEH/nftg2EY1X8m
5QeGSM8xo4qhC49+j409zMV8QPW/n3Qbp7LwU5q/v4cBymToXipiLxt/FPpy3HEu5Rw1eI7YN1KuT2/w1q4k9lpx
NeX3KhlWREXsQ1H6CBP1LhtfTg2cDgsQ6ltu+vyZUsLFn+G/+nVk2D4qoohNLAeIGeDCSVXUgOnKlekVwySctMRJ
K5yyNpHxJUHbUvrXyNA9ZHABQEyG7SVDdlERB8n+s3DWHueccc4RiOCcRKGXhnynjKOcSjLdLu/H8Jb0UszgrAMo
jDO2CrE7G2IQYoqTFpTva2TAdipgGwVToB1UIBQZmAsVsJ0MzgdMAV8wVlIHIQXmMVHv8aln+ZQzfFoFn16pSa/S
ZFSBlDcooxpIk16NNlZrMiR13FijyarRZNZos2q0Ymoi82xR1TDNqrTIOaeNLjCOj6RRkhKxbz/WpUi/TeqgAoBM
HZQPMyANzEcp2FigCshVB+xUgzQQKSBXFZgHNtKjD3OZjVx6HZdRz2c2IDVymQ1woyweictq5LNA2gQycLuYgfkG
Nr2eTqqlkyUxKbV0Sh0Qk1JHJddhcaaqwRI/wcLyFWS3H/XgrCthNZqwHElYRhJWkSqHdBB1qZGHqBGHqJHvIB2i
R6HMKCRDZtS7tKjI90CeiT8BaWbUIdUjyjBFiOtF8UjylgZT8VkNbFqd+fhG90cuuz18yX3OZSCPuZc95lz2nHPZ
bc5nQx+5NH3N5RlrL81Yc2nmmksPPX9hzstXsh49hjmNoTzGkVBjKa+JBO+kDNPQ1yfEaEbYpamDD6gDgIcWqgL3
qEOLufRaLqPBxNFgKqtJ2pLdxGU38VDneDGT1SiRQoJ5saQpRGNG9OgGKYU7bAQO6zjjUlSRMDJfiNwljCqAiiwQ
onYJ4bnCuOL2XwThV0G4gfRvtMjzSONPVNoFq8lXLCZ+bnHXJat7rlFOMWgyjVJitWiEdaxKn0f4vkX4bSF0m1X6
7VxaNUJcx6XXo3hbZ+KVKNNho0ENXMeAzJtEZpPtDZ2CNidVpCbA12nGpci9wvDcdoB4ZB7UqDwhMk+IAHyLJL5i
KvJ9p+p7LOQUFluJjT6LRZ3G4ptwS3+FKehE4awbEMY4q1wmq0P2w3Y2pFAdvBsMbBG+BqNzdVCnLfW3FGjo0NTe
Ir5jMdPKABpf83FNzjMvOU+/5DzjkouomZdcZ1wEmcGzL9298uLklRcnrrgwacWFu1demPTUJzNXX9yw/+rrRVff
2H91/f6v3zrw7eDEN3Hv+ZzucdZvkcrSTxwR9KnhD/RZuwzCbyfwWXzoBmp4KcDEptVKbtgJU5aUGjpLvEmY5bIa
uKzGW+h3clvTjlZD94LlYf8qGXaoYJps6GjBl6TUv6qV0vhaLKIyYk6zgHwZePTPyKNj5n+Bp180H3+h313fUJ53
oa+s7nt80wm/bYTPm/jQN6gRpZBReg2XUSv2flFvR2KKWlvxJWpYEU0JcRYS6hrJ0BvlCtDYkbux8eW7hdsgHdfY
sjd2VJPZGKPMxzapk+rjnvz0R0H4uk34ph2mID9q7jksocIso0I7ppl0y+57/gu7HIRtKuGziRj2Bu79OuAL2j6p
wc2o7+iYhm5VgxF3poRY4mtKvIOHNt6Kkr+d/8qgs0wb5QY+q8sg36jNbsQT60bN+/iL6zear/768Tc3mr+58dm1
X0fObabT6yxzaszGfkp65MAuFkH2od6U873EgGXE4Gfwwa/Qoz/gUiu51CrDiMbIN7NBHpxKLWmH9jRLirp8Vhcx
tpuW+g4RNxoRS33vjj3tju24NqPeNqsWyAak2XVWiWeHLfnH8G2tga/82C+7jos9yI0+QA98sO/c+CD6LyL0uYTv
RnzIa3RcGbxiabVd8zX1l6zOTaph0No9X1kZDb+9pZtOl9GXu3w3s4HNbKQzJDGZjaq4Ku8VV8GoKuSNX8yyariU
Ck32Rdp3UR/i67WA8N0MW17v9XTsMXipYWSuN5F8JWG+lgeNMkrFzG0ltuByRhRo1rtVrZQaP27yQVjlTHZuutt0
uYDc2UOxpUEDRtAJVd7Lvx6V2x60/mfIN7mcB+h9FvQdvqr+TxK6rSqftwjv1yX/7ZJvRj0/5oJm3BXNuC8M4nNQ
KkvaaFDO56CArM/ltDt91vGluJPPjXuTD81LqVjGpPyYi1K0yTAEk0YqoXLYqm9GFwqhb90wy6zmkk9pMs/Tvk/2
pfgM+G5R+WwAPSsG8m3swDezXvRoPruZDl1HDnyQHPQQOehBSQNNNKibfIctDyA9KKemG2/dLn+wOxnfnQ5S2n8l
n3UOeDEvtymgsaCTqj0f+8z/5e+9V3yjhQu2T2uyPqZ1C/uQ/3otJHy3EpDveiZe4ssbnbcODW2aNHddJd3S/9Jf
xNJPO/5LLus8akqkyRbwRdiUWjalBnQq+JRKHvJtZvoUX+S/Uvsbj+IzbFvrZLgNfMopNvYgn3IS8jVM5P4PpEbq
6i10SmqbQD7pKDzVtLOw+5ch38IAQ6rsBg3o+KVUgPjMZ57vW3y9RL7Ifzu1v2mVmrEXGf+nwQXEVexf/YKAgbyK
BmNbduRmfsynXHq1PP5Cd6lAjU0FfE/zWc2079+YLy6n0p0Uwn0ePvRNfMg6MP5F8blenNkAFwS0uZpJ37FBz3Vz
fw2/3bqsP+pODX7rnn/j2/GjCzQTr4GT5zLr0HgNeTHoYCed5hJP8OkNJv1n053/TaevrCdizkswp/m4+3IW1HB4
Mwj6L59RQ3repXZJUVn6/Em/Kvpzai+utglVOydS3g9rxnwMby6LQ/L0Gip8HxWcR4UVk25T/p6zlGito1Hi3Xzb
e3CXZZjTE7jHCuk+IOKryajBaavbuBWupnA13Y0YXEX+EeerwknGdM8EeHlnP4tQWek1OZeAC8tTKLWQb0g+FX5A
LfFVwYlKHAlmVL2VKaGGPRBCTXnez43YzYZuZsK2MaE7CN4TzTzfh7ssxRznQ76pVbDzLPJNryY07rf+PB+AA30Z
sp+tzwv1fq9/rnvt8rB1n/m8CnQZpL6vfqZbd9F/05X+D+fC3xeAOvA7fqWCE5CjSm0RkuX/9hW/Vy/p138GDjT0
pQt+m65YjbwHu/3TexAstd0ITc5FNOkhjvJq4dq/kDwqvFjtfj8sxTtrY4u0ccXa6EKzlCPMgInoBxE0+qVVr3Rt
atBcPrmWjTnMxR7l4k4SZt4y32UwPnuslO/j14IKr8moJnjXbuMYofZ66wevAmFQHnz0gX+hJN1uwXO7MGCf4DD/
/f/8hC0DUnQlgsd2wWMnPJC+AP5y0DZhzm/wRZ6otgu/he8+me9U+A00bhowYkqv02ZUWUz6khpwb2/sSjHuCbzf
LM73QV73gCZyuya1gk86ziWU8UmniH5D4de0myLGZ0LmC+ceu+CLbjORrEPGQodxK9zvXrP29E/bmtoeL2sdvK1F
n9vit/PGsB0tw3e1bKht2Xa+7fGiT60yn3KetNIiZIzhLtUdRRsM6+cT7TxplfOYRQ4z86cevrmtvmVrQ+tjR1sG
bf4lqLjNJm7WnfBVdeTLi+0v5HuARHzJfu6es5u9Hv3c5u4mbc4nNmk7XZPmuyQ96Z6x2EqX3INz/h9GZgyzSM6z
n9diO/2q7czvzCdf4jPrNFl1mswaTWa9ynwY4ns/7voU4rvKhG99Z77ik8R4C/+drf6lwsgSuOQJ2NF/Cl55QtBe
IWCPMHSXkHJIEK3imjCwQAg/InjOK8LkR9n89ikjas53PRt2RAgtFjzzhDfqxIfnCR9eaXffeCO0RLCJn3lH/msb
DgZ3qP0FXSwTvhElpCe8f8TYDBix6Ub0bsHt0S+ZjAavxd+P3iWM3CrE7BMGP5B75+f8v+VrHr/JZuZVq/s/tbz/
osWkZovxTeY5DXBRcUaDysIH8rWfauDLppryremCL2fu++bXvrmtIXm/nvhHW/N3wvoz/+f2fPOwl5u9X2ruv6Z5
+JsXKr+++fG37duabg54++egolb36dt6ytche0nAvhv6bT95bW5ZXN4G9nb+2/Y369u8Nt8IKRFs42fdGd8IA1/Y
i4Z894L4TEeUkp4PgQK0df+gl/4v/K1f3eddMZ/Q3H/hVyPebgl97YcR21oH3P1Wr+J73frBy/3uvTx4xb/CN7YG
vPyj2RjgxU0qS1/E9wHcBfBdgPii/lVGDeSbWUNouuL71jX/XYIu9+aw7S1+RYL73EL0s00Sh78zIhinQX47W3zz
BN+dN3U7bwQUC+4zd/SUr2POyqBDsLXVgwPtbPNF8gE73HEj6ADw39l31v5GwPicWSutvwXDvXDQ/uZTEaVq5L+q
fl5WE8+bjW8etvJ6xMbW0NdvhG9oCXv93yO3tw+49+3ewtciaavN7H/ZTL9iPuVzn+d/isoXwja29BtTr8k6p7LU
ofg8Tfbflaj/jG7VofFRF3x5wPc64KvPu+mX2xJ4QOg/bw96k5Cae4eB/vmtgIs+vw2ADgR8Z+3sKV+71Ed93/5y
6CsXdZu+9S9oh7uCagdNPDiiTcId8VXB+HwB9RUN7e9exPcg6QV/GKUy8zC/6zw3tlm3+ntwWUZsaQMavulG1C5h
0P1begtfPvwls3GfmGXXaLLq7e/7xH32ZZcHL4CxDw/js8zXReS7gk2p5NIgX757vrqN1/x3C34i32LBC/LFkfPC
6SDIN6/Vv8DI16MnfA1DMILRgNEucFXgsOBAgK8e8m0J+r18YXwOA/5bQA0HfGELTmg9zLLAYLDe4b7znrMvus+4
4DHjgvtDzV5zv7SNfqEXTGAiLrTfM2xiFfxBbsIpJv40HXsGiEs+y6fWyu3vNGP/ObUSwoUuXKdJv118hv6L8Hk+
UijhQ8UAX31uq97A9wDg+3v6KuII1Hr0tKASAewHOK/e4L/xPePLi11oI99DZH/EV+OugT+PqmQTztJxZ5h4pLgT
bHIT7bOk1/DVP8slV3MJJ7jEU1zSGT4ZwoUzsSlVKnMf2X9lvuDLptXwQBl1cH6Dvx1f/c4W0Lx6zu3M19+Ub/Hv
5asm4dRp9APQf3e26EW+O3rId8wFeV0H5EuGofYX8PWaIfLlU8+Cr8ynVPCpUGDwqIELAJpYv6W9hq9uBZdYAR9h
FH+CjT/JJpSzCadAyiWdJaTxkWn7C/hWo1HSb/HNhfEZ8d3dke8AEJ8h3zyZ7+zfxRfhs45+UOKb126Mz3faf+7E
t1rqXxn5uvEpp7nkClDtYYWHdx9OA0fgU+sZXa/hywC+CWfYmI9AiIYSKUPQZ+T5DXl85L4C3l9Iq+LTYC9ak17V
JV+dgS/At79zfKYdBuhzW/zzTfjO+iP4Av8FOwT+W3zH/WfbcHSXUORbByqtFJ8jTPw3GcY0GNkSy2GdB5cl7hiX
Usv4LOo1/uv7NBt/mo0+zMYcY2LLgGTQ0vyVyuC/7ivgHfDUah7NctyObwHkq98B+XrN29upfwXaXyPf/cB/834X
XxifbaIfCgZ8dyD/zQPxuUXmi9/u/oUxPst8MxDf8E583fikk2ziaTbhJKr2J+Blif2IS6qihy3sNXx9lrOx5czo
D5joj5joowykfIyB4bpc9l/Y/uKQ70o2pQJOcYh807rhu+EaxLfzJrja8KHBc8XxkTSVx9j199vZ6pcHOthtfjtE
vvm/u39lM3qayNcvFz7DVifyjZtpKPDbfOEaS5GvOP4tQOMjxJd3A9EYNljxoMKXsaDmxx6DD4dJqKCHPtlb+FJD
lzDRx5nId5moD+nRh5nRRyBo8EXiTkp8be+H949kvhzki2Y5uuHr++Y1fb6g23lTt73Ffx/w332YmiYoDidZMKLh
XH2B/wIW+tw2HeLr2XO+8F+WaB5XU7axM4OKAd9WyDe3HRwR7NA2cR44kAoMoLpzYdP2N93At9rAV+05HfF15eLL
YFMFKjys/EBHgCOwcaepIfN7wQ1i0X+HLUP++yE8/5ij0HlBlI45ysad6Mh3Pmx/Id8qxBf0oiu75vv6Vd+dbaG5
LVVX2774UdhQ8YPr85d91302bN1nA1/+fNTmL5u+a/vih/aCj9sGvv1LUFGbx6wexGf5Oc/zfDdcGbr2gu+b1/1B
WN4JpYdpmz63HWwcuuai34YvHVKf6Loh7hCfa6QuVlqV1H8G/tuBL4jJZYwkAPowG3+GGvJEb+FLes2mgnaTAVvh
UzLgEzN2UKG70YPmymS+0v1fwv1pFgydAN9UOMvRDV8L/7d/0hcKI/YIP96E0/7vfS54bReCCoWA3cLQfCGlRLiJ
/mzlxFfCwO1C6CE5gPdofnLMCjDs1eeCRhw4L+JrSLeDyAB6XELQfsEp57nb8FXbhmnGfGLCt5oM3UsG53f032NM
/El6RAkFnxxSQAXlweeKhO4TK8BffoEHuqNnnY27P427LsRcF2GuSzDnRcSA50EtBe4s3f+1uRd3XoI5Pk64LYd8
UyolvqmVt97/BRdTMzBCMyTSMiBt7O5vJ5a2JO35dejmX3VboXy2/hq4/dcJxb9Mfr819qXT7KCR2mHRjJP3nd9r
k+4vZCzx33XDZ9NPvltu6La26La1wFTUlhbd5hs+G//tn3/DMXP5b/nvJ3CVnXhHO7UK9p8h31Kj/8YcAe2v2n87
3n81PuB5vP9zeP9niIGvEHY54k3uXsDXJofwfA73WIZ7LMc9n8bcnyIGrWWiP2Q78F2MOT6Guz3FJp+BfOEsZTUY
9RO8S3fVGKfYwVvbvfcIPoVCYBH8JyyYFgkB+wTvXYJPqeC2+MTvOWMEy2n86uB34UPFA/aiPQPtlQS37BECCoSQ
g4LzuLW35RumyZb5AqVWwgc3BedR4SWkx0OIrwsfexiMjNQB2/GBL6gGvwREDHpB5b1e5TChN/BFprbAKSecdsIp
R9wmB3dfjrstIQasVg18HmfcEN97cOdFmOOjhNsywBc2weIoKeVsl3zFvwBTsWbO2U+5jH/Wedwqx3HPOAHlrAKp
87hnXMatdJu82nb0NAw9XrhnUQ4VNvOJc570HIjSjmCfUM84QsEM1NhVjmNWukxc3c8nvuvqZ4jP2R8jvuiOCeS7
hwQROOyA2uMBWIq2VQ1aDZl6rQQXhLBJxxknnHGGInvlg99xyzTM8wWAGHNfgbk+jVEQH259N+68EHN4FHddxiad
lvlWQb5drb/qBSatv4rgAd80E76hkC8J+U5DIcgG91iFecGwTAxah5uPxHqliYt74eQDrg0lbO/GbSbgthNxm8k4
aYP4TsacnsTs5xEyXzQKruJSzuKUxe1HMbc+rdeo/+TmWlfPAe7iycC3jQwqSx30XzAWQHfE2BSRb67Rf0lzwv4e
3P5e3H4y4TgV1+ikB7v9vZZD49aTIF8HwHcpm3QKdbEAYtCLrqD1yym/paRLirz+Gf+rC50k5TWJ1i2lg15EP8Go
4dCMOqi0ov9Cvmj91d/xKQ34rSv2Zb6PQL6JkC+bXCH1sjKb+AnXmYBnes3VQL10buROzYSrXOY5KThDxDVd8VV1
eUH+ZkZYT8SdFsh8T7NJZ0TEIKABd+Yym2ndErgYnqClpeBGievkTTO3vttJhrduLfybn/rNnaNl3jjJhG/gMs+z
KWdFspL/Jp+V298Stfu0XjD9+IfFZ8AX+i/uupRJLIdNcJLMF46Fq7mkMi76ABtdKoqLPsjFdK9YoEMwjQElO6mE
g3soYUeXsIaMqOgSw/6lQ3Q4yiETGTaWciYfkRQDdJBPOQUHRBCrgW+VxDc4l+zgv32Dr+MCzB76L5NQziTJLpyC
EKeKK3bQj+IzGoF4ELS7VBZ8MqEGCebRRg6JzxBfokeyoJ0YxEv7bJRKSh85h3YI0nN89nk+uxml5+FLoCwp5bJM
DmFyOPj8EOCwSBAunJEDfM+QIYWy//Y1vk/g9nMJ1yXwTllCOWyFTRHDtriCFee1xKfrdJDhAlbQsXCJC0hhC965
mKyUKhbth+2w3bhbeXu1mGESz9KxZ+m4s3S8OLeGpsdFcFIGlRc5piHBUwIfB3zFl1WorRH5gv5zsfrv+vuyLvla
TcAd52OAr8tiJuEEI/Ht0BBDvkYE1Zx4D9GQSathEistJtYPXntt4Oprg1+8BvJgC4yKiD6bKheWrrzEkRUpSNs7
vkRdIybhrNPci2C3A577ZtAL18zG1rLJlXLUrZEp3yrTelLFoSoKvhEZslvme1+f4otBvnMg33jA9yQrtcJnTBCL
qhQ9iO3kkmnVwL9sp54LKxECC4WQEsFm6nk64azUsRHLpxjdEw6+UmCKgoO0RcqIhUXHzKih404PeObr8INC0G4h
pEgwn1DHJFYYOaJbmazRZw1YK+WYUIngopNPPAWfQB6US4YWq13v7Vv+6/AYZof4xpUBxDBKAxeWvFh05DPyuMkI
mpUFp+5jT9tObQwrhZPDoQcFq3sayFEn6LhTdAxQOZN4RqokyaLQB5PllynyWygDCqNPwdWe6uFl/Vf8M/yQELQH
8R0PPRrRrLyd4BSciPUsVDJSQjkJH1G+gwwpUvUtvuMR34dx54VoaUcZEydHaVPKEuKzkkd3FGglNRkV9g802U1r
tLuvbsCqfwx+5drANdcGrbk+cM032qxKUEAOAhXiFKhJZEAU4KRoBZ1wxnJizZB11wathXHee903bk9ctLuvwX7a
ObupjXzaWdi3N+whxVDf0Jxqx5fGsxXPP/4EfMK8/xYyeE/f4muZg9s/itnNxp2fBM5Lgy50/EkUqMslQcqnxaEx
IwZtY+gWLyBMgd9R0eV07ClqxDGfTT+FvQ//UjYE/eu9NrOSjCoHwZaOPU2BAqJikGJP0fIW4O+qkcet7qkLPwL/
aDu4RIj4SHB+5KIqogx+NhqchmkdE6uZiYcadUZOwXmehucMa+Aplf8OlX4zfGi5yz19ie9YMDjCbGfijo/SEXup
iH101HsSYpAmnESUTzGSO4ugTxtDdwfiZzhwteNOeq24MvT174a8fN173XdDXrlmM6XOclKN1d21lpOhrCbVWkLV
iC/hFiRLUGBitcP0hqHrrw9Z9+2QV7712fCt3YNNVGy5BLFzBZNTI1DxlABTpESQngIRiY58lx5ZqvJ5UzX0NZVu
i8phXF/iOwZ0rgBfSRb3q4e9yiSBUUkZDREjyvEiZbR2OtHAWsyfNn0JBUrGHGeiy5ioY8zoMmr44WEbvw8+IATs
aQ8Qb+CKd3L3IKEtYt6/sB347JCXrlKhHzAx4sePMbEn5KN0qlriy1NSKm+EQNEZohM+CaBTEUWYy2LM42nMfRnu
+iTutgy3iOs1t3f/c74WgO9czG4WZj8bc5iLWT2g9lkPHJaO+Uhujo8zHUAbnFokjtJEk/ZadPYkcZx1CpDy2fwD
AGdYAxC4H66uMaZFUOLaABDMh7xynY48xiaLTT+qQmIbAXdrPBAjZTqnYLscfI7T4OQTy0FEwgDT/s/iXitwjyW4
50rcKrEv8c3A7GbgdjMx+4ehLKYg/62A681EoPHlEuW4EzLojrhRyiYY0AOdEFcUw2IxR60nn7WfWmN3f4391GqY
QlXLqjHIbkqN/bRqqwln6ehjzC2hQ9yz4ShshyMiV42XMwb/jT8J+gbQf12X4P1X4Z5P4e4LcLdFuEVM3+GLqTQY
ocGtU2HFdlmAWd6H289UeSwh3BcRMF2sHvIi7HcZfFlUvChT3PAlG294C66iR4WPUZEfUaOOAJFIUmYkFDUKvoUy
ME+Cl1EfoQOVdXUIMHaDYuKN9QemaCPMgMMllKu9XyI8lhJeywnPZVCuCzDraTj4OoNexC1GYSotpuKwvmO4OEuZ
hIOv774Ys5iMmU/CrKZi1g9iNg+BcE24LoSxzrC2NrZMuv6mrA35uLKOQlvEZahIIM8Y81KGTZDTBFTAsCuT2mIq
Vq5Ocr06LmXiykB7Qbgtxawfgi2O7XTM5gEAF7O8F3w1YsiruFmw4Sv3Gb5oaYdNCjF4Haj2mMVEzPJuzG46Zj8L
c3gY9LhU7kvgZZf810BHdiiDEk6wJr5sICg5soFOpyphQNMFrxPSZAtqAlgpJiP/RRmTiC0dHf4MNrFc5bUCNDS4
4zzMcS7mMBu3nYpb3avyWKge8hLRL6T3PKvtDzW1GVxdRtljakuMtMNtpsDuFmiU7Wbh9rNBiMNdFxJQiwk3ELSB
lspa1o1MCywFPgUFM0s6auktOxRLijt5yiSzHKYeT6k8lhMey1Vwi6mkg6o8luJOj2KOj2AOczDHefDpbep+uMoM
pqQlTjCYYjiB29wn8QUpzMzC7GZDwT7YHBOBywg0D+mRLgR65qBPbi9qDsobPivuStw+RyrpIJeHGcOeAS8xfcwg
XMo8apTDozhMHwGeC50X7MHpMdx2ksJTboZlETRuOwW3n4ODKOcwF8oRXLrHCMfH4c/QRDk/STgvJFwWdUglLYQv
YYEFuNMC9JH5QDj8+OMwlfRYhwx4C5RxegKl88FnZRn2vJhwXUK4LlUB73ZdAlO3ZWiLqMXyoZ9Ee3gcUZ5P2N3X
Fx4i2kMjcNIWpxxkOcIV1OIiatoFp12hGLcuRRjysBgo7GyUuBNJnV523A7LywfqfCx3IAKpQwaWdzEekXKST1v5
O2DF+na4Fv9aFP27KM65EhovQuOJ0lvlCcVLgkvyxA/2eI0rgalYgnPHOTccpDyQB9yneBRtf4NUoswG4aQZOsMu
j6XYHTInmYg8Ds4UHeMSjnNw0FoGFXeMA1tiP4JPLo05zEZ/wEa9x446RHAu2O8YbYr/p6YdTA8vocOL6OHFzKhD
dOS78DdT4BDioRNP8smn+KRyLvkUl3yST6tT247oO7cM/pQxMuhUExQ7Mp8DVzX+KJcArnMZF3cUkpXgfgjJAo1+
D/BlRpWinx/iPf7XXfhgagLyjThARxTREfvpkaVM5DvM6PfhIWKPcHEf8UknNMnlQNrEk9r4Mk1ihcpmuPQ8Z8Vh
e27kwMdI/XrSdy0dUUhHHqTC92sn1lvOu2o//6rv+h91b/w09OXvzMbWarLrtGMkaTKr2LDtdPBbdOCbBOt8B86F
HjZOWZPDVpM+a0m/16iwvVRYIRW+j089w2dUajKrtVk12uxaPr1q8Jpr+k3/Hrzuxw2nfjj66Y8nL/wU9eD7eNB2
s5hitWOcPGmj2B3z9XmeDC9Vh+xRh+ymIvapg3aZ3dtks+zfzqt+Dt0rhBcLQflCvwnntDlNZuOaQKoF6dgGNuYo
fFBA1Ifig8TviC9tRwYXkKF71cG7qeB8uJw1uECTUaUBdWZsgzYHqJHPrNVv+SXioOC3S6j4SWhBT5cdu7QZG3nM
POsc6T5W4dvjya0hy9XBBSr9ZjqylE08ysa8bzaxzmLWF9azPvd6+uv+K696Lvun2bh66LljpVSTXcNGvcNEHmRG
lhC8ew/4Bm6Bq6SCc5nIUnpUCRN1SJNZqcmq0WTXSpEho8pj0eeDn7/qsfLreTu+eqHoqzV7/6GfVkXFHTPPrqfc
sxW+PebrvVIdsk+l38anV2rvuqCd8Il2XLNm7HnN2GYuq5nLPM9nnTPLadaO+1hWszbnHBt9hI76gBn1bs/8NzBX
HVQAmgBNBnqSbQYI+w2aMSAsnNfCQwChte5Z5zXZzaq0c1hyI5bYwGWf65fTaD7xCuU1QeHbY74eM9VDXyCHrOQi
8zWJBzUJJZqkg9qkQ9rkQ2bJ75ilvKNNRhsTSrUJpTATV6yJ2ccEb6QD36ADXu9J+2ul9n6W9H6O9nuZiyrkInfz
UYV8TJEmdj/cZ/wBbUKJNrFUm3LIDMk89R2L1EMWKQfNwMa4IrPkDyjXZIXv7xoOo9Gl+HPvHvzFGNHzMYvJsX77
H81u2aj0nxVTrAdTW93pv3w4ZbZKMcUUU0wxxRRTTDHFFFNMMcUUU0wxxRRTTDHFFFNMMcUUU0wxxRRTTDHFFFNM
McUUU0wxxRRTTDHFFFNMMcUUU0wxxRRTTDHFFFNMMcUUU6zX2/8Dn6S5DQ==
"""


def load_pixels() -> list[list[tuple[int, int, int]]]:
    raw = zlib.decompress(base64.b64decode(DATA_B64))
    expected = SOURCE_WIDTH * SOURCE_HEIGHT * 3
    if len(raw) != expected:
        raise RuntimeError(f"Embedded sprite has {len(raw)} bytes, expected {expected}")

    rows: list[list[tuple[int, int, int]]] = []
    i = 0
    for _ in range(SOURCE_HEIGHT):
        row: list[tuple[int, int, int]] = []
        for _ in range(SOURCE_WIDTH):
            row.append((raw[i], raw[i + 1], raw[i + 2]))
            i += 3
        rows.append(row)
    return rows


def resize_nearest(
    pixels: list[list[tuple[int, int, int]]],
    target_w: int,
    target_h: int,
) -> list[list[tuple[int, int, int]]]:
    src_h = len(pixels)
    src_w = len(pixels[0])

    if target_w == src_w and target_h == src_h:
        return pixels

    out: list[list[tuple[int, int, int]]] = []
    for y in range(target_h):
        sy = min(src_h - 1, round(y * (src_h - 1) / max(1, target_h - 1)))
        row: list[tuple[int, int, int]] = []
        for x in range(target_w):
            sx = min(src_w - 1, round(x * (src_w - 1) / max(1, target_w - 1)))
            row.append(pixels[sy][sx])
        out.append(row)
    return out


def ansi_fg(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"\033[38;2;{r};{g};{b}m"


def ansi_bg(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"\033[48;2;{r};{g};{b}m"


def luma(rgb: tuple[int, int, int]) -> int:
    r, g, b = rgb
    return int(0.2126 * r + 0.7152 * g + 0.0722 * b)


def render_truecolor(
    pixels: list[list[tuple[int, int, int]]],
    *,
    center: bool,
) -> str:
    h = len(pixels)
    w = len(pixels[0])
    terminal_cols = shutil.get_terminal_size((w, 24)).columns
    pad = max(0, (terminal_cols - w) // 2) if center else 0
    prefix = " " * pad

    lines: list[str] = []

    for y in range(0, h, 2):
        parts: list[str] = [prefix]
        last_fg: tuple[int, int, int] | None = None
        last_bg: tuple[int, int, int] | None = None

        for x in range(w):
            top = pixels[y][x]
            bottom = pixels[y + 1][x] if y + 1 < h else (0, 0, 0)

            if top != last_fg:
                parts.append(ansi_fg(top))
                last_fg = top

            if bottom != last_bg:
                parts.append(ansi_bg(bottom))
                last_bg = bottom

            parts.append("▀")

        parts.append(RESET)
        lines.append("".join(parts))

    return "\n".join(lines)


def render_plain(
    pixels: list[list[tuple[int, int, int]]],
    *,
    center: bool,
) -> str:
    ramp = " .:-=+*#%@"
    h = len(pixels)
    w = len(pixels[0])
    terminal_cols = shutil.get_terminal_size((w, 24)).columns
    pad = max(0, (terminal_cols - w) // 2) if center else 0
    prefix = " " * pad

    lines: list[str] = []
    for y in range(0, h, 2):
        chars = [prefix]
        for x in range(w):
            top = pixels[y][x]
            bottom = pixels[y + 1][x] if y + 1 < h else (0, 0, 0)
            value = (_luma(top) + _luma(bottom)) // 2
            chars.append(ramp[min(len(ramp) - 1, value * len(ramp) // 256)])
        lines.append("".join(chars).rstrip())

    return "\n".join(lines)


# Backward-compatible alias used inside render_plain.
_luma = luma


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the Bit mascot in the terminal.")
    parser.add_argument(
        "--cols",
        type=int,
        default=DEFAULT_COLS,
        help="Output width in terminal columns. Canonical exact value: 160. "
             "Use 128 or 96 for smaller terminals.",
    )
    parser.add_argument(
        "--fit",
        action="store_true",
        help="Fit to current terminal width, capped at 160 columns.",
    )
    parser.add_argument(
        "--no-center",
        action="store_true",
        help="Do not horizontally center the mascot.",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Use monochrome fallback instead of ANSI truecolor.",
    )
    parser.add_argument(
        "--check-size",
        action="store_true",
        help="Print rendered dimensions and exit.",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    terminal_cols = shutil.get_terminal_size((DEFAULT_COLS, 24)).columns
    cols = min(DEFAULT_COLS, terminal_cols) if args.fit else args.cols
    cols = max(24, cols)
    rows_pixels = cols if cols % 2 == 0 else cols + 1

    pixels = load_pixels()
    pixels = resize_nearest(pixels, cols, rows_pixels)

    if args.check_size:
        print(f"{cols} columns x {rows_pixels // 2} terminal rows")
        return 0

    if os.name == "nt":
        # Enables ANSI truecolor in modern Windows terminals.
        os.system("")

    if args.plain:
        print(render_plain(pixels, center=not args.no_center))
    else:
        print(render_truecolor(pixels, center=not args.no_center))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
