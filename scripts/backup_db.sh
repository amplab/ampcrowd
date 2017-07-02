#! /bin/bash

# rysnc the contents of the ampcrowd database to s3.
# Add this file to your crontab to perform regular backups.
# Note: the AWS CLI tools must be installed and configured
# to run this script.
DB_NAMES=( "ampcrowd" "crowderstats" )
SYNC_FOLDER="/mnt/backup"
S3_BUCKET="s3://com.ampcrowd.experiments"

for DB_NAME in "${DB_NAMES[@]}"
do
    echo "Dumping contents of database $DB_NAME..."
    DUMP_FILE="$SYNC_FOLDER/database/$DB_NAME.db"
    pg_dump $DB_NAME > $DUMP_FILE
    echo "Done."
done

echo "All databases dumped. rsync'ing to s3..."
aws s3 sync $SYNC_FOLDER $S3_BUCKET
echo "All done."
