import * as THREE from '../vendor/three.module.js';
import { OrbitControls } from '../vendor/OrbitControls.js';

// Geometry shared with the approved WiZ bedroom, in metres.
export function createBedroomScene(canvas) {
  const fixtures = [{p:[-1.22,1.35,-1.37]}, {p:[-.30,2.12,-1.54]}, {p:[.10,2.12,-1.54]}, {p:[1.26,1.24,-1.27]}];
  const renderer = new THREE.WebGLRenderer({canvas, antialias:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
  renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.25;

  const scene=new THREE.Scene();scene.background=new THREE.Color('#202823');
  const camera=new THREE.PerspectiveCamera(39,1,.05,50);camera.position.set(-3.9,3.9,4.7);
  const orbit=new OrbitControls(camera,renderer.domElement);orbit.target.set(0,.65,0);orbit.enableDamping=true;orbit.minDistance=3;orbit.maxDistance=11;orbit.maxPolarAngle=Math.PI*.48;orbit.enablePan=false;
  scene.add(new THREE.HemisphereLight('#fff3dc','#5c6857',1.3));
  const sun=new THREE.DirectionalLight('#fff1d8',2.0);sun.position.set(-3,6,3);sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);sun.shadow.camera.left=-4;sun.shadow.camera.right=4;sun.shadow.camera.top=4;sun.shadow.camera.bottom=-4;sun.shadow.normalBias=.025;scene.add(sun);
  const mat=(color,extra={})=>new THREE.MeshStandardMaterial({color,roughness:.85,...extra});
  const wood=mat('#754c31'),dark=mat('#282b27'),cream=mat('#d5cbb2'),sage=mat('#9da996'),tile=mat('#b9b5a3'),oak=mat('#b39b75');
  function mesh(geometry,material,x,y,z,parent=scene){const m=new THREE.Mesh(geometry,material);m.position.set(x,y,z);m.castShadow=true;m.receiveShadow=true;parent.add(m);return m;}
  const box=(x,y,z,w,h,d,material,parent)=>mesh(new THREE.BoxGeometry(w,h,d),material,x,y,z,parent);
  const cylinder=(x,y,z,rt,rb,h,material,parent)=>mesh(new THREE.CylinderGeometry(rt,rb,h,24),material,x,y,z,parent);
  // Dimensions in metres: 10 ft × 11 ft. Back wall contains the window and headboard.
  box(0,-.10,0,3.15,.18,3.45,mat('#454c40'));
  box(0,0,0,3.05,.03,3.35,tile);
  for(let x=-1.525;x<1.53;x+=.50)box(x,.018,0,.006,.003,3.35,mat('#8f917f'));
  for(let z=-1.675;z<1.68;z+=.50)box(0,.018,z,3.05,.003,.006,mat('#8f917f'));
  const walls=[];
  const back=new THREE.Group();scene.add(back);walls.push({group:back,side:'back'});
  box(0,1.28,-1.72,3.15,2.56,.08,cream,back);box(0,.08,-1.66,3.05,.13,.035,oak,back);
  const right=new THREE.Group();scene.add(right);walls.push({group:right,side:'right'});
  box(1.57,1.28,0,.08,2.56,3.35,cream,right);
  // Cutaway left wall exposes two door positions, as seen in the photos.
  box(-1.57,.16,0,.08,.32,3.35,cream);
  for(const z of [.55,1.28]){box(-1.51,.035,z,.03,.012,.62,wood);box(-1.48,.14,z-.32,.055,.27,.035,oak);box(-1.48,.14,z+.32,.055,.27,.035,oak);}
  // Window, pleated blue curtains and wall fitting.
  box(-.25,1.25,-1.66,1.78,1.7,.045,mat('#dad7c9'),back);
  for(let i=0;i<32;i++) {const c=box(-1.1+i*.054,1.14,-1.60+Math.sin(i)*.025,.058,1.79,.055,mat(i%3?'#294b6a':'#557283'),back);c.castShadow=false;}
  box(-.24,2.08,-1.55,1.92,.025,.025,dark,back);
  box(-.1,2.1,-1.62,.69,.12,.04,mat('#b4b1a3'),back);
  // King mattress 1.83 × 1.98 m, carved wooden headboard, pillows and coverlet.
  box(-.22,.20,-.57,1.91,.38,2.10,wood);box(-.22,.43,-.57,1.83,.20,1.98,sage);
  box(-.22,.68,-1.58,1.99,.90,.10,wood);
  for(let row=0;row<3;row++)for(let col=0;col<10;col++)box(-1.09+col*.191,.50+row*.20,-1.514,.085,.095,.018,mat('#ab8450'));
  box(-.72,.565,-1.20,.61,.10,.36,mat('#ece4d0'));box(.24,.565,-1.20,.61,.10,.36,mat('#d4cfcc'));
  box(-.22,.55,-.12,1.84,.025,.70,mat('#72877c'));
  for(let i=0;i<13;i++)box(-1.10+i*.144,.548,-.63,.008,.005,.63,mat('#c4cdbb'));
  // Small dark table left of bed, near floor lamp.
  box(-1.29,.65,-.78,.40,.06,.73,wood);
  for(const x of [-1.44,-1.14])for(const z of [-1.07,-.49])box(x,.31,z,.035,.62,.035,dark);
  // Desk nook in the back-right corner; Mac, chair and wall shelves.
  box(1.22,.73,-1.17,.52,.055,.92,oak);
  for(const z of [-1.56,-.78])box(1.35,.36,z,.035,.72,.035,dark);
  box(1.15,.77,-1.02,.28,.025,.23,mat('#414947'));
  box(1.15,.87,-1.13,.28,.19,.016,mat('#1d292e'));
  box(1.15,.875,-1.118,.25,.16,.008,mat('#416e78',{emissive:'#274655',emissiveIntensity:.3}));
  box(1.10,.43,-.48,.34,.09,.36,dark);box(1.10,.70,-.31,.34,.49,.065,dark);cylinder(1.1,.20,-.48,.025,.025,.4,dark);
  box(1.26,1.75,-1.54,.45,.035,.23,wood);box(1.25,2.33,-1.33,.52,.43,.62,oak,right);
  // Wardrobe extends along the right wall, mirror and drawers facing into room.
  box(1.30,1.12,.58,.44,2.24,2.04,oak,right);
  for(const z of [-.37,.08,.53,.98,1.43])box(1.07,1.11,z,.012,2.10,.012,wood,right);
  box(1.066,1.31,.58,.016,1.38,.42,mat('#abc0b9',{metalness:.72,roughness:.24}),right);
  box(1.055,.37,.58,.025,.31,.43,wood,right);
  for(const z of [-.15,1.13])box(1.04,1.10,z,.035,.21,.017,dark,right);
  // Small desk against the wall beyond the foot of the bed.
  box(-.45,.50,1.38,.72,.06,.44,wood);
  for(const x of [-.75,-.15])for(const z of [1.22,1.54])box(x,.25,z,.035,.48,.035,dark);
  // Ceiling fan is represented above the room; it is decorative, not a control.
  const fanMat=mat('#777e70',{transparent:true,opacity:.38});cylinder(-.2,2.35,-.15,.10,.10,.10,fanMat);
  for(let i=0;i<3;i++){const blade=box(-.2,2.39,-.15,.10,.025,.83,fanMat);blade.geometry.translate(0,0,.27);blade.rotation.y=i*Math.PI*2/3;}
  // Four individually selectable light fixtures and physical point-light sources.
  const pickables=[];
  fixtures.forEach((f,index)=>{
    const [x,y,z]=f.p,shadeMat=mat('#eee4cb',{emissive:'#ffd5a1',emissiveIntensity:0,side:THREE.DoubleSide});
    if(index===0){
      cylinder(x,.68,z,.018,.018,1.28,dark);
      for(let j=0;j<3;j++){const leg=cylinder(x,.37,z,.015,.015,.80,wood);leg.rotation.z=.20*Math.cos(j*2.094);leg.rotation.x=.20*Math.sin(j*2.094);}
      f.shade=mesh(new THREE.CylinderGeometry(.14,.20,.30,32,1,true),shadeMat,x,y,z);
    }else if(index===3){cylinder(x,1.03,z,.018,.018,.35,dark);cylinder(x,.84,z,.10,.10,.02,dark);f.shade=mesh(new THREE.CylinderGeometry(.09,.12,.29,32,1,true),shadeMat,x,y,z);}
    else{f.shade=mesh(new THREE.CapsuleGeometry(.047,.14,6,12),shadeMat,x,y,z);cylinder(x,y-.13,z,.035,.035,.08,dark);}
    f.shade.userData.fixture=index;pickables.push(f.shade);
    f.light=new THREE.PointLight('#ffe1b5',0,4,2);f.light.position.set(x,y,z+.07);scene.add(f.light);
    f.anchor=new THREE.Vector3(x,y+.2,z);
  });

  // User-confirmed positions: Node 2 at upper-right desk, Node 1 by shared wall.
  const sensorMat=mat('#24c4cc',{emissive:'#24c4cc',emissiveIntensity:.8});
  box(1.15,.79,-1.02,.10,.025,.055,sensorMat);
  box(-.45,.54,1.38,.10,.025,.055,sensorMat);
  const signalMaterial = new THREE.MeshBasicMaterial({color:'#24c4cc',transparent:true,opacity:0,depthWrite:false,side:THREE.DoubleSide});
  const signalRings=[[1.15,.82,-1.02],[-.45,.57,1.38]].map(([x,y,z])=>{
    const ring=mesh(new THREE.RingGeometry(.08,.15,48),signalMaterial,x,y,z);
    ring.rotation.x=-Math.PI/2;ring.castShadow=false;ring.receiveShadow=false;return ring;
  });
  const activityMaterial = new THREE.MeshBasicMaterial({color:'#ef8859',transparent:true,opacity:0,depthWrite:false,side:THREE.DoubleSide});
  const activity = mesh(new THREE.PlaneGeometry(3.03,3.33),activityMaterial,0,.025,0);
  activity.rotation.x=-Math.PI/2;activity.castShadow=false;activity.receiveShadow=false;
  const resize = new ResizeObserver(()=>{
    const {width:w,height:h}=canvas.getBoundingClientRect();
    if(!w || !h)return;
    renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();
  });
  resize.observe(canvas);
  renderer.setAnimationLoop(()=>{
    if(document.hidden || !canvas.getClientRects().length)return;
    orbit.update();back.visible=camera.position.z>-.8;right.visible=camera.position.x<.8;
    renderer.render(scene,camera);
  });
  return {
    view(name){
      const presets={reset:[-3.9,3.9,4.7],top:[0,6.1,.01],front:[0,2.4,4.0]};
      camera.position.set(...(presets[name]||presets.reset));orbit.target.set(0,.65,0);orbit.update();
    },
    setActivity(energy){
      const level=Math.max(0,Math.min(1,energy));
      activityMaterial.opacity=level*.28;
    },
    setSignal(change){
      const level=Math.max(0,Math.min(1,change/15));
      signalMaterial.opacity=level*.7;
      signalRings.forEach(ring=>ring.scale.setScalar(.8+level*1.4));
    },
    dispose(){
      renderer.setAnimationLoop(null);resize.disconnect();orbit.dispose();
      const materials=new Set();
      scene.traverse(o=>{o.geometry?.dispose();if(o.material)for(const m of [o.material].flat())materials.add(m);if(o.shadow)o.shadow.dispose();});
      materials.forEach(m=>m.dispose());renderer.dispose();
    }
  };
}
