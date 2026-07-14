@author r4nd0m4gent

The Textile Ecosystem Living Lab (TELL) is a tool aiming to represent the textile ecosystem in the Netherlands.
Data structure and website features are replicable/scalable for EU-wide application.

Main features:
  - Search engine of textile businesses and related organizations, using geographical location, market segments, keywords and other clusters.
  - Contribution form allowing users to leave feedback, or to suggest specific edits and additions to the DB.
  - Semantic classifier that creates custom classifications based on keywords specified by the user.

-------------- DEPLOY ------------------

# deploy.sh – run this on a fresh Ubuntu 22.04 / 24.04 Digital Ocean Droplet
# as root (or with sudo). Replace the placeholders marked with <...>.

# DB credentials - via DO (hvafashion@gmail.com)
username = doadmin
password = ********************
host = db-mysql-ams3-65711-do-user-10023598-0.f.db.ondigitalocean.com
port = 25060
database = defaultdb
sslmode = REQUIRED


-------------- REDEPLOY ----------------

# 1. On Windows: commit and push
git add .
git commit -m "Add year/legal charts, responsive layout, cleanup"
git push origin main

# 2. SSH into the server
ssh root@tell.newtexeco.nl   # or root@<server-ip>

# 3. Insert password in the terminal

# 4. Launch the redeploy.sh file
sudo bash /home/tell/app/deploy/redeploy.sh
